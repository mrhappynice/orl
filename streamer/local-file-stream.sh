ffmpeg -re -i "ALtRS — KISS U.m4a" -vn -c:a aac -b:a 96k -f mpegts "srt://openradio.live:9000?mode=caller&transtype=live&streamid=live&passphrase=PASSWORD-HERE&pbkeylen=32"
