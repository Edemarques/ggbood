import networkx as nx
def solve(number_of_nodes, adjacency_list, time_budget=0, seed=None):
    g = nx.from_dict_of_lists({i: adjacency_list[i] for i in range(number_of_nodes)})
    return list(nx.approximation.max_clique(g))
