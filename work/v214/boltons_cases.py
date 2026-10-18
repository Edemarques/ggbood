from boltons.iterutils import chunked, windowed, pairwise, bucketize, partition, unique, redundant, split, strip, first, one, same, flatten, backoff, remap, get_path, research

def case_chunked():
    return chunked(range(10), 3, fill=None)

def case_windowed():
    return windowed(range(5), 3)

def case_pairwise_end():
    return pairwise([1, 2, 3], end=0)

def case_bucketize():
    return bucketize(range(10), key=lambda x: x % 3)

def case_partition():
    return partition(range(10), key=lambda x: x > 4)

def case_unique():
    return unique([1, 2, 1, 3, 2])

def case_redundant():
    return redundant([1, 2, 1, 3, 2], groups=True)

def case_split():
    return split(["a", None, "b", None, "c"], maxsplit=1)

def case_strip():
    return strip([0, 1, 2, 0], 0)

def case_first():
    return [first([0, None, 3]), first([], default=9), one([0, 1]), same([1, 1])]

def case_flatten():
    return flatten([1, [2, [3, 4]], 5])

def case_backoff():
    return backoff(1, 16, count=6)

def case_remap():
    return remap({"a": 1, "b": [2, 3]}, visit=lambda p, k, v: (k, v * 2) if isinstance(v, int) else True)

def case_get_path():
    return get_path({"a": [1, {"b": 2}]}, ("a", 1, "b"))

def case_get_path_missing():
    return get_path({"a": 1}, ("x",), default=5)

def case_research():
    return research({"a": 1, "b": {"c": 2}}, query=lambda p, k, v: v == 2)
