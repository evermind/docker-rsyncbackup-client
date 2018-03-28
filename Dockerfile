FROM alpine:3.7

RUN apk add --no-cache --update openssh-client python py-libmount rsync

ADD rsyncbackup-client.py /usr/local/bin/rsyncbackup-client.py

ENTRYPOINT ["/usr/local/bin/rsyncbackup-client.py"]
