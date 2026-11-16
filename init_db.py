"""数据库初始化脚本。

用法：
    python init_db.py              # 建表建索引（幂等，不影响已有数据）
    python init_db.py --seed       # 建表并写入 3 条示例数据
    python init_db.py --reset      # 删除并重建表（会清空全部数据）
    python init_db.py --reset --seed
"""

from __future__ import annotations

import argparse

import config
import db


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化词云工坊 SQLite 数据库")
    parser.add_argument("--reset", action="store_true", help="删除并重建所有表")
    parser.add_argument("--seed", action="store_true", help="写入示例数据")
    args = parser.parse_args()

    if args.reset:
        answer = input("将清空所有爬取数据，确认继续？(y/N) ").strip().lower()
        if answer != "y":
            print("已取消。")
            return

    result = db.init_db(reset=args.reset, seed=args.seed)
    print(f"数据库文件: {config.DATABASE_FILE}")
    print(f"数据表: {', '.join(result['tables'])}")
    if args.seed:
        print(f"已写入示例数据: {result.get('seeded', 0)} 条")
    print(f"当前数据总量: {result['total']} 条")


if __name__ == "__main__":
    main()
