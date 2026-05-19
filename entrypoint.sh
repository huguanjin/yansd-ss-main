#!/bin/sh
set -e

# 初始化数据目录
mkdir -p /data

# 启动 supervisord（管理 ss-manager + web）
exec supervisord -c /etc/supervisord.conf
