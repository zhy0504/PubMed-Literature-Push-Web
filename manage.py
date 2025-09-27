#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flask-Migrate管理脚本
用于数据库迁移管理
"""

import os
from flask.cli import FlaskGroup
from app import app, db

def create_app():
    """创建Flask应用实例"""
    return app

cli = FlaskGroup(app)

if __name__ == '__main__':
    cli()