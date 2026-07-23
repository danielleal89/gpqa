import argparse
import os
import sqlite3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', dest='db_path', required=True)
    parser.add_argument('--out', dest='out_path', required=True)
    args = parser.parse_args()

    db_path = os.path.abspath(args.db_path)
    out_path = os.path.abspath(args.out_path)

    conn = sqlite3.connect(db_path)
    try:
        dump_lines = list(conn.iterdump())
    finally:
        conn.close()

    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as handle:
        for line in dump_lines:
            handle.write(line)
            handle.write('\n')


if __name__ == '__main__':
    main()
