from app.core import cluster


def test_ensure_cluster_key_is_stable():
    a = cluster.ensure_cluster_key()
    b = cluster.ensure_cluster_key()
    assert a == b
    assert len(a) >= 32
    path = cluster.key_path()
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600
