
docker rm -f yansd-ss && docker rmi ghcr.io/huguanjin/yansd-ss-main:latest && docker run -d   --name yansd-ss   --network host   --restart unless-stopped  -v /usr/ss-data:/data   ghcr.io/huguanjin/yansd-ss-main:latest
