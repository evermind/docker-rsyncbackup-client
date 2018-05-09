#!/usr/bin/env python

import logging, os, sys, argparse, subprocess, time, re
import libmount as mnt
from datetime import datetime, timedelta

def setup_logging():
	logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)

UNSET = object()
def get_env(name,default=UNSET):
	if name in os.environ:
		return os.environ[name]
	if default is not UNSET:
		return default
	logging.error("Missing environment variable %s",name)
	sys.exit(os.EX_USAGE)

def get_rancher_host_label(label_name):
	try:
		import requests
		response=requests.get('http://rancher-metadata/latest/self/host/labels/%s' % label_name)
		if response.status_code==200:
			return response.text
	except:
		pass
	return None

def get_rancher_host_name(default=None):
	try:
		import requests
		response=requests.get('http://rancher-metadata/latest/self/host/name')
		if response.status_code==200:
			return response.text
	except:
		pass
	return default


def setup():
	setup_logging()
	backup_server=get_env('BACKUP_SERVER',None)
	if backup_server is None:
		backup_server=get_rancher_host_label('backup_server')
		logging.info('Got BACKUP_SERVER=%s from rancher host label "backup_server"',backup_server)
	if backup_server is None:
		logging.error("Missing environment variable BACKUP_SERVER")
		sys.exit(os.EX_USAGE)
	config={
		'server':		backup_server,
		'port':			int(get_env('BACKUP_SERVER_PORT',22)),
		'server_key':	get_env('BACKUP_SERVER_PUBLIC_KEY',None),
		'user':			get_env('BACKUP_SERVER_USER','backup'),
		'volume_dir':	get_env('BACKUP_VOLUME_DIR','/data/volumes'),
		'mount_dir':	get_env('BACKUP_MOUNT_DIR','/data/mounts'),
		'conf_dir':		get_env('BACKUP_CONF_DIR','/data/conf'),
		'volumes':		[]
	}
	logging.info("Using server %s@%s:%s",config['user'],config['server'],config['port'])
	timeout_regex = re.compile(r'(\d+)([hms])')
	for vol in filter(None,get_env('BACKUP_VOLUMES').split(',')):
		timeout_str=get_env('VOL_%s_TIMEOUT'%vol,'24h')
		timeout_parts = timeout_regex.match(timeout_str)
		if not timeout_parts:
			logging.error("Invalid timeout: %s",timeout_str)
		timeout=int(timeout_parts.group(1))
		if timeout_parts.group(2)=='m':
			timeout=timeout * 60
		elif timeout_parts.group(2)=='h':
			timeout=timeout * 3600
		
		vc={
		  'vol':	   vol,
		  'dir':	   get_env('VOL_%s_PATH'%vol,os.path.join(config['volume_dir'], vol)),
		  'excludes':  filter(None,get_env('VOL_%s_EXCLUDE'%vol,'').split(",")),
		  'timeout':   timeout
		}
		config['volumes'].append(vc)
		
		logging.info("Volume %s at %s",vc['vol'],vc['dir'])
		logging.info("  Timeout set to %s",timeout_str)
		for exclude in vc['excludes']:
			logging.info("  Excluding %s",exclude)
		
	return config

def run_backups(config,volumes=[]):
	for vol in config['volumes']:
		if (len(volumes)==0) or (vol['vol'] in volumes):
			run_backup(config,vol)

def check_backup_ready(volpath,volume):
	readyfile = os.path.join(volpath,'.ready_for_backup')
	if not os.path.exists(readyfile):
		logging.warn('[%s] ERROR! Indicator file %s does not exist in - skipping this backup.',volume['vol'],readyfile)
		return False
	return True

def run_backup(config,volume):
	volname = volume['vol']

	volpath=volume['mount_dir']
	
	logging.info("[%s] Starting backup of %s, mountet to %s",volname,volume['dir'],volpath)

	if not volpath.endswith("/"):
		volpath+="/"

	if not check_backup_ready(volpath,volume):
		return

	# build rsync command
	cmd=[]
	if os.path.exists('/usr/bin/nice'):
		cmd+=['/usr/bin/nice','-n','19']
	elif os.path.exists('/bin/nice'):
		cmd+=['/bin/nice','-n','19']

	if os.path.exists('/usr/bin/ionice'):
		cmd+=['/usr/bin/ionice','-c','3']
	elif os.path.exists('/bin/ionice'):
		cmd+=['/bin/ionice','-c','3']

	# backup timeout
	cmd+=["/usr/bin/timeout",'-t',str(volume['timeout'])]
	
	# rsync with options
	cmd.append("/usr/bin/rsync")
	cmd.append("-e")
	cmd.append("ssh -p %s -o HostKeyAlgorithms=ssh-rsa -o UserKnownHostsFile=%s/known_hosts -o IdentityFile=/%s/id_rsa" %
	  (config['port'],config['conf_dir'],config['conf_dir']))
	cmd.append("-avr")
	cmd.append("--numeric-ids")
	cmd.append("--delete-during")
	cmd.append("--acls")
	cmd.append("--xattrs") # TODO: Skip if not supported or make configurable
	cmd.append("--sparse")
	
	# append excludes
	for exclude in volume['excludes']:
		cmd.append("--exclude")
		cmd.append(exclude)
	
	# source
	cmd.append(volpath)
	
	# destination
	cmd.append("%s@%s:%s" % (config['user'],config['server'],volume['vol']))
	
	# ensure that we use our own key for backup, not the one passed via ssh agent by the current user
	myenv=os.environ.copy();
	myenv['SSH_AUTH_SOCK']=""
	
	logging.info("[%s] Running '%s'",volname,"' '".join(cmd))
	
	# execute the rsync command
	rsyncExitValue=-1
	try:
		p = None # Starting the process might fail -> ensure that p is defined
		p=subprocess.Popen(cmd,env=myenv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
		for line in iter(p.stdout.readline,''):
			logging.warn("[%s] %s",volname,line.rstrip())
		p.wait()
		rsyncExitValue=p.returncode
	except KeyboardInterrupt:
		rsyncExitValue=20
		pass
	finally:
		if p:
			try:
				p.terminate()
				time.sleep(1)
				p.kill()
			except OSError:
				pass # Process might already be terminated

	if rsyncExitValue==124:
		logging.warn("[%s] ERROR! The backup timed out after %s",volname,volume['timeout'])
		return
	
	# rsync exit code 0 - everything was ok
	# rsync exit code 24 - everything was ok but some files changed during sync
	if rsyncExitValue!=0 and rsyncExitValue!=24:
		logging.info('[%s] ERROR! rsync exited with code %d - this backup is failed.',volname,rsyncExitValue)
		return

	logging.info('[%s] backup ok - tell the server that we are done.',volname)

	cmd=["/usr/bin/ssh"]
	cmd.append("-o")
	cmd.append("HostKeyAlgorithms=ssh-rsa")
	cmd.append("-o")
	cmd.append("UserKnownHostsFile=%s/known_hosts" % config['conf_dir'])
	cmd.append("-o")
	cmd.append("IdentityFile=%s/id_rsa" % config['conf_dir'])
	cmd.append("-p")
	cmd.append(str(config['port']))
	cmd.append("%s@%s" % (config['user'],config['server']))
	cmd.append("FINISH_BACKUP")
	cmd.append(volume['vol'])
	subprocess.call(cmd,env=myenv)

def bind_mount(src,dst):
	if os.path.ismount(dst):
		logging.debug("Volume %s is already mounted to %s",src,dst)
		return

	if not os.path.exists(dst):
		os.makedirs(dst)

	logging.info("Bind-mounting volume %s to %s",src,dst)
	ctx = mnt.Context()
	ctx.options="bind"
	ctx.source=src
	ctx.target=dst
	try:
		ctx.mount()
	except Exception as e:
		logging.error('Failed to mount: %s. Please ensure that you are running as root with docker capability SYS_ADMIN.',e)
		sys.exit(os.EX_IOERR)

def mount_dirs(config):
	for volume in config['volumes']:
		voldir=volume['dir']
		volume['mount_dir']=os.path.join(config['mount_dir'],volume['vol'])
		bind_mount(voldir,volume['mount_dir'])

def setup_ssh(config):
	confdir=config['conf_dir']
	if not os.path.exists(confdir):
		os.makedirs(confdir)
	pubkeys=config['server_key']
	if pubkeys:
		logging.info('Creating %s/known_hosts' % confdir)
		known_hosts=[]
		for pubkey in filter(None,list(pubkeys.split())):
			pubkey=pubkey.strip()
			if len(pubkey)==0:
				continue
			key_parts=pubkey.split(':')
			logging.info('  Adding %s %s',key_parts[0],key_parts[1])
			known_hosts.append('[%s]:%s %s %s'%(config['server'],config['port'],key_parts[0],key_parts[1]))
		with open(os.path.join(confdir,'known_hosts'),'w+') as file:
			file.write('\n'.join(known_hosts))
			file.write('\n')

	if not os.path.exists(os.path.join(confdir,'id_rsa')):
		logging.info('Creating %s/id_rsa'%confdir)
		subprocess.call(['ssh-keygen','-N','','-t','rsa','-f',os.path.join(confdir,'id_rsa')])

	with open(os.path.join(confdir,'id_rsa.pub'),'r') as file:
		ssh_key=file.read().split()
		logging.info('Use the following key on the backup server:')
		client_name=get_rancher_host_name('[BACKUP_CLIENT_NAME]')
		logging.info('  %s:%s:%s',client_name,ssh_key[0],ssh_key[1])

def get_next_schedule(hour,minute):
	now = datetime.now()
	schedule = now.replace(hour=hour,minute=minute,second=0,microsecond=0)
	while schedule < now:
	  schedule = schedule + timedelta(days=1)
	return schedule


def schedule_backups(config,hour,minute):
	while True:
		schedule = get_next_schedule(hour,minute)
		logging.info("Scheduled next backup at %s",schedule)
		while schedule > datetime.now():
			time.sleep(10)
		run_backups(config)

def main():
	config=setup()
	setup_ssh(config)
	mount_dirs(config)

	def time_type(s, pat=re.compile(r"(\d{1,2}):(\d{2})")):
		if s=='auto':
			s=get_rancher_host_label('backup_schedule'):
			if is None:
				raise argparse.ArgumentTypeError("Got 'auto' as schedule time but found no rancher host label 'backup_schedule'.")
		time = pat.match(s)
		if not time:
		   raise argparse.ArgumentTypeError("Invalid time format")
		return {'hour':int(time.group(1)),'minute':int(time.group(2))}
	
	parser = argparse.ArgumentParser(description='Rsyncbackup client')
	sp = parser.add_subparsers()
	sp_run = sp.add_parser('run', help='Run backup for one or more volumes')
	sp_run.set_defaults(action='run')
	sp_run.add_argument('volumes',metavar='VOLUME', nargs='*', help='An optional list of volumes to backup',
	  choices=[[]]+[v['vol'] for v in config['volumes']])
	sp_cron = sp.add_parser('schedule', help='Schedules backups at a given time of the day')
	sp_cron.add_argument('time',metavar='HH:MM', help='The time of day to schedule the backup', type=time_type)
	sp_cron.set_defaults(action='cron')
	args = parser.parse_args()
	
	if args.action=='run':
		run_backups(config,args.volumes)
	else:
		schedule_backups(config,args.time['hour'],args.time['minute'])

if __name__ == "__main__":
	main()
