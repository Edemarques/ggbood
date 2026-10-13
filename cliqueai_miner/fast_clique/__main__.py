# 入口：python -m fast_clique
# 重的 import 都放在 main() 里，这样 spawn 出来的子进程导入本文件时不会去加载 bittensor。


def main():
    from fast_clique.miner import main as run_miner

    run_miner()


if __name__ == "__main__":
    main()
