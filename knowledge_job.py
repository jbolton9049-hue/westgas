# -*- coding: utf-8 -*-
"""供 Windows 任务计划程序调用的无界面知识治理入口。"""

import argparse
import maintenance


def main():
    parser = argparse.ArgumentParser(description="天然气管理知识库周期治理")
    parser.add_argument("kind", choices=["daily", "weekly", "monthly", "publish"])
    args = parser.parse_args()
    maintenance.run_job(args.kind)


if __name__ == "__main__":
    main()
