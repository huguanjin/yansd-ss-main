FROM shadowsocks/shadowsocks-libev:latest

USER root

# 安装 Python、pip、supervisord
RUN apk add --no-cache \
    python3 \
    py3-pip \
    supervisor \
    && python3 -m venv /opt/venv

# 安装 Python 依赖
COPY app/requirements.txt /app/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# 复制应用代码
COPY app/ /app/
COPY subfile.yaml /app/subfile.yaml
COPY supervisord.conf /etc/supervisord.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 创建数据目录
RUN mkdir -p /data /var/log/supervisor

# Web 管理端口（默认 15231）
EXPOSE 15231

ENV SS_MANAGER_PORT=6001
ENV WEB_PORT=15231
ENV SS_METHOD=chacha20-ietf-poly1305

# WEB_SECRET 已移除，管理员密码在首次访问时通过 Web 界面设置

VOLUME /data

ENTRYPOINT ["/entrypoint.sh"]
