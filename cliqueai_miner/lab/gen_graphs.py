"""Build the benchmark graph set: CliqueAI test_data + seeded G(n,p) graphs. Format: 'n m' then 'u v' per edge."""
import json, os, random, sys
CLIQUEAI = sys.argv[1]
sys.path.insert(0, CLIQUEAI)
from CliqueAI.graph.codec import GraphCodec

def write(name, n, edges):
    with open(f"graphs/{name}.txt", "w") as f:
        f.write(f"{n} {len(edges)}\n")
        f.writelines(f"{u} {v}\n" for u, v in edges)

codec = GraphCodec()
for fn, name in [("general_0.2.json", "test298"), ("general_0.4.json", "test493")]:
    d = json.load(open(os.path.join(CLIQUEAI, "test_data", fn)))
    m = codec.decode_matrix(d["encoded_matrix"])
    write(name, len(m), codec.matrix_to_edge_list(m))

for n, p, s in [(300, 0.735, 1), (500, 0.735, 2), (700, 0.735, 3), (900, 0.735, 4),
                (500, 0.5, 5), (500, 0.9, 6), (900, 0.85, 7), (900, 0.3, 8)]:
    rng = random.Random(s)
    edges = [(i, j) for i in range(n) for j in range(i + 1, n) if rng.random() < p]
    write(f"g{n}_p{int(p*1000)}", n, edges)
