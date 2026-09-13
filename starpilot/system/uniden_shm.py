import os

SHM_DIR = "/dev/shm/params/d"

def get_shm_param(name, default):
    p = os.path.join(SHM_DIR, name)
    try:
        if os.path.exists(p):
            with open(p, "r") as f:
                v = f.read().strip()
            if isinstance(default, bool):
                return v == "1" or v.lower() == "true"
            elif isinstance(default, int):
                return int(v)
            elif isinstance(default, float):
                return float(v)
            return v
    except Exception:
        pass
    return default

def set_shm_param(name, value):
    try:
        os.makedirs(SHM_DIR, exist_ok=True)
        p = os.path.join(SHM_DIR, name)
        with open(p, "w") as f:
            if isinstance(value, bool):
                f.write("1" if value else "0")
            else:
                f.write(str(value))
        return True
    except Exception:
        return False

