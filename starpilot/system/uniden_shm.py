import os
from openpilot.common.params import Params

params_memory = Params(memory=True)

def get_shm_param(name, default):
    try:
        if isinstance(default, bool):
            return params_memory.get_bool(name)
        val = params_memory.get(name)
        if val is None:
            return default
        val_str = val.decode("utf-8") if isinstance(val, bytes) else str(val)
        if isinstance(default, int):
            return int(val_str)
        if isinstance(default, float):
            return float(val_str)
        return val_str
    except Exception:
        # Fallback to direct /dev/shm/params/d read
        p = os.path.join("/dev/shm/params/d", name)
        if os.path.exists(p):
            try:
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
                return default
        return default

def set_shm_param(name, value):
    try:
        if isinstance(value, bool):
            params_memory.put_bool(name, value)
        else:
            params_memory.put(name, str(value))
        return True
    except Exception:
        # Fallback to direct /dev/shm/params/d write
        try:
            os.makedirs("/dev/shm/params/d", exist_ok=True)
            p = os.path.join("/dev/shm/params/d", name)
            with open(p, "w") as f:
                if isinstance(value, bool):
                    f.write("1" if value else "0")
                else:
                    f.write(str(value))
            return True
        except Exception:
            return False

