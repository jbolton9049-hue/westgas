# -*- coding: utf-8 -*-
"""打包脚本：把工具打包成可执行文件。
用法：
  Windows:  python -m PyInstaller --onefile --windowed --name 天然气管理工具 main.py
  Mac:      python -m PyInstaller --onefile --windowed --name 天然气管理工具 main.py
打包后在 dist/ 目录生成可执行文件。配置文件会在首次运行时保存在可执行文件旁边，
避免单文件模式下写入临时解压目录而丢失配置。
"""
