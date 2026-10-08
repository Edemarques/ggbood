cd ~/tgopt/S2-perf
for rep in 1 2; do
for h in 50000 300000; do
  for n in 500 5000 50000; do
    python3 gcf.py nofreeze $h $n | tail -1
    python3 gcf.py freeze $h $n | tail -1
  done
done
done
