"""Local, versioned K1/K2 sequences. Loading a file never starts a sequence."""
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

MAX_STEPS = 100
MAX_WAIT_MS = 3_600_000
MAX_FILE_BYTES = 32 * 1024
FORMAT = "power-module-relay-sequence"


@dataclass(frozen=True, slots=True)
class RelayStep:
    wait_ms: int
    k1: bool | None
    k2: bool | None
    output: bool | None = None

    def __post_init__(self):
        if type(self.wait_ms) is not int or not 0 <= self.wait_ms <= MAX_WAIT_MS:
            raise ValueError("等待时间须为 0～3600000 毫秒的整数。")
        if any(value is not None and type(value) is not bool for value in (self.k1, self.k2, self.output)):
            raise ValueError("开关动作必须为布尔值或保持不变。")


def validate_steps(steps):
    snapshot = tuple(steps)
    if not 1 <= len(snapshot) <= MAX_STEPS or any(type(s) is not RelayStep for s in snapshot):
        raise ValueError(f"请设置 1～{MAX_STEPS} 个步骤。")
    if all(s.k1 is None and s.k2 is None and s.output is None for s in snapshot):
        raise ValueError("时序至少要有一个开关动作。")
    return snapshot


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
            or type(data.get("version")) is not int or data["version"] not in (1, 2)
            or not isinstance(data.get("steps"), list) or not 1 <= len(data["steps"]) <= MAX_STEPS):
        raise ValueError("文件不是受支持的 PowerModule 继电器时序。")
    steps = []
    keys = {"wait_ms", "k1", "k2"} | ({"output"} if data["version"] == 2 else set())
    for index, row in enumerate(data["steps"], 1):
        if not isinstance(row, dict) or row.keys() != keys:
            raise ValueError(f"第 {index} 步缺少字段或包含未知字段。")
        try:
            steps.append(RelayStep(row["wait_ms"], row["k1"], row["k2"], row.get("output")))
        except ValueError as error:
            raise ValueError(f"第 {index} 步：{error}") from error
    return validate_steps(steps)


def save_sequence(filename, steps):
    data = {"format": FORMAT, "version": 2, "steps": [
        {"wait_ms": step.wait_ms, "k1": step.k1, "k2": step.k2, "output": step.output}
        for step in validate_steps(steps)
    ]}
    path = Path(filename)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, indent=2)
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
