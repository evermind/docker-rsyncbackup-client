FROM alpine:3.8

RUN apk add --no-cache --update openssh-client python py-libmount py-requests rsync encfs

ADD rsyncbackup-client.py /usr/local/bin/rsyncbackup-client.py

ENTRYPOINT ["/usr/local/bin/rsyncbackup-client.py"]
