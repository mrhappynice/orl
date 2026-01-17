ffmpeg -f pulse -i default -vn -c:a aac -b:a 96k -f mpegts \
"srt://openradio.live:9000?mode=caller&transtype=live&streamid=live&passphrase=PASSWORD-HERE&pbkeylen=32"
