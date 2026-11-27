"""Local, versioned physical and named relay sequences. Loading a file never starts a sequence."""
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

MAX_STEPS = 100
MAX_WAIT_MS = 3_600_000
MAX_FILE_BYTES = 32 * 1024
FORMAT = "power-module-relay-sequence"
MAX_RELAY_NAME = 24
MAX_RELAY_NAMES = 100


def valid_relay_name(name):
    return (isinstance(name, str) and 0 < len(name) <= MAX_RELAY_NAME
            and name == name.strip()
            and not any(unicodedata.category(c).startswith("C") for c in name))


def relay_name_key(name):
    return unicodedata.normalize("NFKC", name).casefold()


@dataclass(frozen=True, slots=True)
class RelayNames:
    """One local name catalog, with one-to-one assignments to the two ports."""
    names: tuple = ()
    bindings: tuple = (None, None)

    def __post_init__(self):
        if (type(self.names) is not tuple or len(self.names) > MAX_RELAY_NAMES
                or not all(valid_relay_name(n) for n in self.names)
                or len({relay_name_key(n) for n in self.names}) != len(self.names)):
            raise ValueError("继电器名称无效、重复或超过数量上限。")
        if (type(self.bindings) is not tuple or len(self.bindings) != 2
                or any(n is not None and n not in self.names for n in self.bindings)
                or self.bindings[0] is not None and self.bindings[0] == self.bindings[1]):
            raise ValueError("K1 和 K2 不能绑定同一个名称，且名称必须在列表内。")

    def encode(self):
        return json.dumps({"names": self.names, "bindings": self.bindings}, ensure_ascii=False)

    @classmethod
    def decode(cls, raw):
        data = json.loads(raw)
        if not isinstance(data, dict) or any(type(data.get(k)) is not list for k in ("names", "bindings")):
            raise ValueError("继电器名称设置无效。")
        return cls(tuple(data["names"]), tuple(data["bindings"]))



@dataclass(frozen=True, slots=True)
class RelayStep:
    wait_ms: int
    k1: bool | None
    k2: bool | None
    output: bool | None = None
    named: tuple = ()
    order: tuple = (1, 2)  # Resolved execution only; never stored in JSON.

    def __post_init__(self):
        if self.order not in ((1, 2), (2, 1)):
            raise ValueError("执行顺序无效。")
        if type(self.wait_ms) is not int or not 0 <= self.wait_ms <= MAX_WAIT_MS:
            raise ValueError("等待时间须为 0～3600000 毫秒的整数。")
        if any(value is not None and type(value) is not bool for value in (self.k1, self.k2, self.output)):
            raise ValueError("开关动作必须为布尔值或保持不变。")
        if (type(self.named) is not tuple or len(self.named) > 2
                or any(type(a) is not tuple or len(a) != 2 or not valid_relay_name(a[0])
                       or type(a[1]) is not bool for a in self.named)):
            raise ValueError("继电器名称或动作无效。")
        if len({relay_name_key(n) for n, _ in self.named}) != len(self.named):
            raise ValueError("同一步不能重复控制同一个继电器名称。")



def validate_steps(steps):
    snapshot = tuple(steps)
    if not 1 <= len(snapshot) <= MAX_STEPS or any(type(s) is not RelayStep for s in snapshot):
        raise ValueError(f"请设置 1～{MAX_STEPS} 个步骤。")
    physical = any(s.k1 is not None or s.k2 is not None for s in snapshot)
    if physical and any(s.named for s in snapshot):
        raise ValueError("同一个时序不能混用物理通道和继电器名称。")
    if all(s.k1 is None and s.k2 is None and s.output is None and not s.named for s in snapshot):
        raise ValueError("时序至少要有一个开关动作。")
    return snapshot



def resolve_steps(steps, names):
    """Resolve the entire sequence before any commands, using only exact bindings."""
    snapshot = validate_steps(steps)
    missing = dict.fromkeys(name for step in snapshot for name, _ in step.named
                            if name not in names.bindings)
    if missing:
        raise ValueError("请先绑定继电器名称：" + "、".join(missing))
    result = []
    for step in snapshot:
        if not step.named:
            result.append(step)
            continue
        actions = dict(step.named)
        if len(step.named) == 2:
            order = tuple(names.bindings.index(n) + 1 for n, _ in step.named)
        else:
            order = (1, 2)
        result.append(RelayStep(step.wait_ms, *(actions.get(n) for n in names.bindings), step.output, order=order))
    return tuple(result)


def load_sequence(filename):
    with Path(filename).open("rb") as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("时序文件过大。")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError) as error:
        raise ValueError("文件不是有效的时序 JSON。") from error
    if (not isinstance(data, dict) or data.get("format") != FORMAT
            or type(data.get("version")) is not int or data["version"] not in (1, 2, 3)
            or not isinstance(data.get("steps"), list) or not 1 <= len(data["steps"]) <= MAX_STEPS):
        raise ValueError("文件不是受支持的 PowerModule 继电器时序。")
    steps = []
    version = data["version"]
    keys = {"wait_ms", "output", "relays"} if version == 3 else (
        {"wait_ms", "k1", "k2"} | ({"output"} if version == 2 else set())
    )
    for index, row in enumerate(data["steps"], 1):
        if not isinstance(row, dict) or row.keys() != keys:
            raise ValueError(f"第 {index} 步缺少字段或包含未知字段。")
        try:
            if version == 3:
                actions = row["relays"]
                if type(actions) is not list or any(type(a) is not list for a in actions):
                    raise ValueError("继电器动作列表无效。")
                steps.append(RelayStep(row["wait_ms"], None, None, row["output"], tuple(map(tuple, actions))))
            else:
                steps.append(RelayStep(row["wait_ms"], row["k1"], row["k2"], row.get("output")))
        except ValueError as error:
            raise ValueError(f"第 {index} 步：{error}") from error
    return validate_steps(steps)


def save_sequence(filename, steps):
    snapshot = validate_steps(steps)
    named = any(step.named for step in snapshot)
    data = {"format": FORMAT, "version": 3 if named else 2, "steps": [
        {"wait_ms": step.wait_ms, "output": step.output,
         **({"relays": step.named} if named else {"k1": step.k1, "k2": step.k2})}
        for step in snapshot
    ]}
    path = Path(filename)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            options = {"ensure_ascii": False, "separators": (",", ":")} if named else {"indent": 2}
            json.dump(data, output, **options)
            output.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def output_profile(state):
    """Return the exact group and target read back from the device, not editor values."""
    applied = state.get("applied", {})
    group, voltage, current = (applied.get(key) for key in ("group", "voltage_mV", "current_mA"))
    if (type(group) is int and 0 <= group <= 10
            and type(voltage) is int and voltage > 0 and type(current) is int and current > 0):
        return group, voltage, current
    return None


def sequence_directory():
    # PyInstaller extracts to a transient directory; use the actual EXE location.
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return base / "sequences"


class SequenceStore:
    """Only named JSON files in the application's per-user sequence directory."""
    def __init__(self, directory):
        self.directory = Path(directory)

    @staticmethod
    def valid_name(name):
        return (0 < len(name) <= 64 and name == name.strip() and not name.endswith(".")
                and not any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)
                and name.split(".", 1)[0].upper() not in {
                    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                    *(prefix + digit for prefix in ("COM", "LPT") for digit in "0123456789¹²³"),
                })

    def names(self):
        try:
            files = list(self.directory.iterdir())
        except FileNotFoundError:
            return []
        return sorted((p.stem for p in files if p.suffix == ".json" and self.valid_name(p.stem)
                       and not p.is_symlink() and p.is_file()), key=lambda name: (name.casefold(), name))

    def load(self, name):
        if not self.valid_name(name):
            raise ValueError("无效的时序名称。")
        path = self.directory / (name + ".json")
        if path.is_symlink():
            raise ValueError("不支持加载链接文件。")
        return load_sequence(path)

    def save(self, name, steps, original=None):
        name = name.strip()
        if not self.valid_name(name):
            raise ValueError("名称须为 1～64 字，不能包含路径符号或使用系统保留名。")
        self.directory.mkdir(parents=True, exist_ok=True)
        matches = [p for p in self.directory.iterdir()
                   if p.name.casefold() == (name + ".json").casefold()]
        if matches:
            path, *others = matches
            if others or path.stem != original or path.is_symlink() or not path.is_file():
                raise ValueError("已有同名时序，请换个名称，或从列表打开它再编辑。")
        else:
            path = self.directory / (name + ".json")
        save_sequence(path, steps)
        return path.stem
