#!/bin/sh
# dump.sh - kvdump's output and messages for the sample files.
here=`pwd`
kvdump="$here/../tools/kvdump"
cd "${srcdir:-.}" || exit 1
status=0

"$kvdump" sample.conf > "$here/dump-sample.got" 2> "$here/dump-sample-warn.got" || status=1
cmp "$here/dump-sample.got" sample.out || status=1
cmp "$here/dump-sample-warn.got" sample.warn || status=1

"$kvdump" -t -s server sample.conf > "$here/dump-server.got" 2> /dev/null || status=1
cmp "$here/dump-server.got" sample-server.out || status=1

if "$kvdump" -q broken.conf > /dev/null 2> "$here/dump-broken.got"; then
    status=1
fi
cmp "$here/dump-broken.got" broken.warn || status=1

exit $status
