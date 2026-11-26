# ESP32 固件

适用于本项目 V1.5 接线的 ESP32-WROOM-32E 双继电器板。ESP32 通过 Modbus RTU 控制 SX150C，通过 BLE 和 CH340E 接收同一套命令。引脚与默认参数集中在配置文件。 [固件入口](PowerModule/PowerModule.ino) · [引脚与默认参数](PowerModule/Config.h)

## 板上接口

| 用途 | ESP32 连接 | 配置 |
| --- | --- | --- |
| K1 / K2 | GPIO16 / GPIO17 | 高电平吸合，启动时释放，线圈命令单独报告 |
| HW-221 OE | GPIO27 | UART1、UART2 初始化后置高 |
| SX150C | UART2 TX18 / RX19 | 默认地址 1，9600 8N1。支持配置为 115200 8N1 |
| CH340E 有线控制 | UART1 TX32 / RX33 | v1.1.7 起使用 921600 8N1，经 HW-221 的 A3/A4 通道 |
| 下载和维护 | UART0 TX1 / RX3 | 3.3V TTL，维护终端 115200 8N1 |
| 手机和电脑蓝牙 | BLE GATT | 广播名 `PM-xxxx`，支持一个中心设备 |

SX150C 通信电平与 CH340E 的 VBUS 隔离按实物核对后接线。Modbus 寄存器地址参考模块说明书第 5～7 页。 [硬件接线表](../docs/简化硬件接线表.md) · [SX150C 说明书](../modules/SX150/SX150C.pdf)

## 构建和烧录

Arduino CLI 的 `power_module` 配置固定 ESP32 Arduino Core 3.3.12、NimBLE-Arduino 2.3.8、ArduinoJson 7.4.2。使用 Arduino IDE 时，打开固件入口，安装相同版本并选择 **ESP32 Dev Module**、**4 MB Flash**、**DIO**、**40 MHz** 和默认分区。 [构建配置](PowerModule/sketch.yaml) · [ESP32 Arduino Core](https://github.com/espressif/arduino-esp32/releases/tag/3.3.12) · [NimBLE-Arduino](https://github.com/h2zero/NimBLE-Arduino/releases/tag/2.3.8) · [ArduinoJson](https://github.com/bblanchon/ArduinoJson/releases/tag/v7.4.2)

通过 COM3 读取本机 Flash ID，实物容量为 4 MB（32 Mbit）。默认分区下单个应用分区为 1.25 MiB。

在项目根目录重建固件。

```powershell
arduino-cli compile --profile power_module --build-path .work/esp32-build firmware/PowerModule
```

烧录使用板载 UART0 下载口。3.3V TTL 适配器 TX 接板 RX，RX 接板 TX，共地。将板上 IO0 接地，复位进入下载模式。将 `COM7` 替换为适配器端口，并将下载的 HEX 放到 `firmware/build/`。Intel HEX 镜像自带各段地址，按下面命令更新时保留 NVS。烧录后松开 IO0 并复位运行。 [烧录镜像][firmware-hex] · [ESP32 串口下载模式](https://docs.espressif.com/projects/esptool/en/latest/esp32/advanced-topics/boot-mode-selection.html) · [esptool HEX 格式](https://docs.espressif.com/projects/esptool/en/latest/esp32/esptool/basic-commands.html#hex-output-format)

```powershell
python -m pip install esptool==5.3.1
python -m esptool --chip esp32 --port COM7 --baud 115200 --before no-reset --after no-reset write-flash 0x0 firmware/build/PowerModule-ESP32.hex
```

如需从编译产物更新 HEX，在 `.work/esp32-build` 目录运行以下命令。

```powershell
New-Item -ItemType Directory -Path ../../firmware/build -Force | Out-Null
python -m esptool --chip esp32 merge-bin --format hex -o ../../firmware/build/PowerModule-ESP32.hex '@flash_args'
```

回到项目根目录后，更新客户端使用的应用镜像。

```powershell
New-Item -ItemType Directory -Path firmware/build -Force | Out-Null
Copy-Item .work/esp32-build/PowerModule.ino.bin firmware/build/PowerModule-ESP32.bin -Force
```

## 通过 COM4 或 BLE 升级

设备首次使用升级功能需要通过下载口安装 v1.2.0 或更新固件。之后连接 CH340E 有线串口或 BLE，在 PC 客户端的「固件升级」中选择应用镜像。 [客户端升级 BIN][firmware-bin] · [下载口烧录 HEX][firmware-hex]

[firmware-bin]: https://github.com/chuckie0329/power_module/releases/download/v1.2.1/PowerModule-ESP32.bin
[firmware-hex]: https://github.com/chuckie0329/power_module/releases/download/v1.2.1/PowerModule-ESP32.hex

升级要求主输出和两路继电器已读回关闭。固件检查应用镜像类型，将数据顺序写入空闲应用分区，并检查完整 SHA-256 和 ESP32 应用镜像。传输中断或取消可从头重试。设备在 45 秒没有收到固件数据后清理未完成的传输。校验通过后切换启动分区，客户端会重新连接并确认新版本。新版本启动后约两分钟未获得确认时，设备会尝试回到上一个有效版本。升级只写应用分区和启动记录，地址、波特率及蓝牙配对等设置保存在 NVS。[ESP-IDF 应用升级与回退](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/ota.html)

## 首次联调

1. 在 SX150C 面板选 M0，设置上电输出关闭、OPP 75.0W、LVP 约 18V、OTP 60℃。OVP 和 OCP 按负载设置。确认 SX150C 断电重启后保持关闭，再核对菜单里的通信地址和波特率。
2. 用 UART0 发送 `info` 检查设备版本。ESP32 初始配置为地址 1、9600 8N1。本机实测 SX150C 使用地址 1、115200 8N1，初次设定时在 UART0 发送 `sx 1 115200`，保存后复位 ESP32。
3. 有线联调时连接后板 CH340E，执行下方的 USB 检查。使用 v1.1.5 及以上固件时，在系统蓝牙设置中添加 `PM-xxxx`，首次配对输入固定 PIN **112233**。设备上电且蓝牙空闲时，可随时发起配对。配对成功后，客户端可直接连接并控制设备。

```powershell
python -m pip install pyserial==3.5
python firmware/tools/usb_check.py COM8
python firmware/tools/usb_check.py COM8 --set-off 5000 100
python firmware/tools/usb_check.py COM8 --relay-test 1
```

第一条检查命令读取设备信息和状态。第二条写入并读回 5.00 V、0.10 A，保持 SX 输出关闭。第三条吸合 K1 约 0.4 秒，换成 2 可测 K2。脚本在写入测试结束时发送断开命令。 [USB 联调脚本](tools/usb_check.py)

## 输出控制

实机 `MODEL=4008`、`VERSION=100` 的面板限流 2.000 A 对应 `I-SET`（`0x0001`）原值 2000。ESP32 固件 v1.0.1 对这一组合按 1mA/计数读写限流，并在恢复 v1.0.0 保存的设定时换算到同一物理限流值。运行电流 `IOUT` 的非零读值待带载核对。 [寄存器记录](../docs/Type-C可调直流电源详细设计书.md#72-核心寄存器)

v1.1.0 可调出 M0～M10。切换前读取目标组的 M-OUT，确认调出时保持关闭，然后关断正在运行的主输出，通过 `0x001D` 调出目标组并读回。主输出关闭时可写入并读回当前组的 OVP 和 OCP，寄存器为 `0x0053 + 组号×0x10` 和 `0x0054 + 组号×0x10`。OCP 原值超过 820 且在 8200 以内时可根据 8.2 A 上限确定为 1 mA/计数。原值可对应两种单位时，用 SX150C 面板当前的阈值校对，固件按模块型号和版本保存匹配的单位。 [Windows 客户端说明](../pc_client/README.md)

从 v1.1.1 起可在主输出关闭时独立写入和读回当前组的 OPP，寄存器为 `0x0055 + 组号×0x10`，步进 0.1 W。v1.1.3 支持 0.1～100.0 W，v1.1.1 和 v1.1.2 的上限为 75.0 W。`protection.opp_mW` 报告设备的实际读回值，包含出厂设定 160.0 W。

- 开机先释放两路继电器，发送 SX OFF 并读回。显式保存过的电压和限流只在确认 SX 关闭后恢复。
- 电压 0.50～40.00 V，限流 0～8.00 A，步进分别为 10 mV 和 10 mA。设定电压乘限流不超过 75 W。开启前核对当前组和保护状态，输入须在 18～22 V 且高于该组 LVP。v1.1.3 要求该组 OPP 在 0.1～100.0 W 之间，且上电输出设置为关闭。
- v1.1.3 支持在运行中应用电压和限流。先写需要降低的限流，再改电压，最后写需要提高的限流。正常写入并读回后保持输出开启。若 SX150C 在调整中触发保护并关断，返回保护状态，等待用户处理。
- 常规档按 160 ms 周期轮询 SX 状态，包含前面板的设定和开关变化。10、20、30 Hz 档的 SX 轮询目标周期分别为 80、40、26 ms。两条链路同时使用时依据较快的档位轮询。RTU 超时初值 200 ms，读和普通写最多重试一次，ON 只发一次。连续 3 次通信失败或 1 秒未取得有效状态时尝试 SX OFF。读不到开关结果时 `applied.enabled` 为 `null`，上次测量值带时间戳和 `fresh:false`。
- 执行 `all_off` 时先释放 K1/K2，再尝试 SX OFF 并读回。从 v1.2.1 起，客户端断开或失联时保留主输出和两个继电器的状态。SX 串口尚未恢复时继续报告主输出状态未知，恢复后核对关断结果。
- `measured.power_mW` 是 SX 的输出功率。100W 整机输入预算由外部仪表实测。温度显示待核对实机寄存器编码后接入。

## 客户端协议 v1

USB 和 BLE 都传输 UTF-8 JSON，完整消息以 LF 结尾，最多 1024 字节含 LF。每条消息包含 `crc32`，值为 IEEE CRC32 的八位十六进制。计算时去掉此字段，递归按键名字典序排序对象，使用无多余空格的 UTF-8 JSON，校验范围不含结尾 LF。数值使用整数，布尔值使用 JSON 的 `true` / `false`。USB 可收到异步 `type:"state"`，用 `type:"reply"` 和 `id` 匹配命令响应。

命令信封字段为 `v:1`、非零 32 位无符号 `id`、`op`、对象 `args` 和 `crc32`。控制命令还带客户端在连接时自行生成的非零 32 位无符号 `client_id`，用于标识会话和处理重复请求。需要核对修订号的命令在信封中另带 `expected_rev`。状态和响应提供 `state_rev`、`mode`、`applied`、`measured`、当前组的 `protection` 和 `fault`。`mode` 为 `initializing`、`off`、`running`、`protect`、`stopping` 或 `unknown`。继电器状态为线圈命令。

| 操作 | 参数和结果 |
| --- | --- |
| `get_info` | `args:{}`，返回 `data.device_id`、固件版本、能力、此链路的 `data.stream_period_ms` 和 `data.min_stream_period_ms` |
| `get_state` | `args:{}`，返回完整状态及最新 `state_rev` |
| `set_stream_period` | `args:{period_ms}`，周期由链路和 SX150C 串口配置决定。依 `get_info` 返回的 `min_stream_period_ms`，可从 33、50、100、200、500、1000、2000 ms 中选择。33 表示 30 Hz。低 MTU 的 BLE 链路使用 2500 ms。正常链路默认 500 ms |
| `set_output` | `args:{voltage_mV,current_mA,enabled}`，校验修订号，写入并读回 |
| `set_relay` | `args:{channel,enabled}`，通道为 1 或 2，设置绝对状态 |
| `select_group` | `args:{group}`，组号 0～10，带 `expected_rev`，切换后读回当前组和主输出关闭状态 |
| `output_off` | `args:{}`，优先关断主输出并读回 |
| `set_protection` | `args:{ovp_mV,ocp_mA}`，带 `expected_rev`，主输出关闭时写入并读回当前组阈值 |
| `set_opp` | `args:{opp_mW}`，带 `expected_rev`，主输出关闭时写入并读回当前组 OPP。v1.1.3 允许 100～100000 mW，步进 100 mW。固件通过 `data.opp_write` 报告写入能力 |
| `match_ocp` | `args:{ocp_mA}` 提供面板当前阈值，带 `expected_rev`，匹配 OCP 寄存器单位 |
| `all_off` | `args:{}`，优先释放两路继电器并请求 SX OFF |
| `disconnect` | `args:{}`，结束连接时保留主输出和继电器状态 |
| `heartbeat` | 兼容旧客户端，USB 用 `args:{}` 及同一个 `client_id`，BLE 用 HEARTBEAT 特征。v1.2.1 起忽略心跳内容 |
| `save_config` | `args:{}`，将当前 M0 的读回设定写入 ESP32 NVS |

USB 和已配对的 BLE 连接均可直接控制。v1.2.1 起，主输出和继电器由设备保持，重连后客户端读取当前状态。新版客户端连接 v1.2.0 及更早的可控固件时继续发送兼容心跳。

同一连接中递增使用修改命令 ID。最近六条已处理命令的同内容重复请求返回原响应，改用其他内容会返回 `ID_REUSED`，淘汰的旧 ID 返回 `OLD_ID`。客户端等待一条修改命令的结果再发送下一条。`all_off` 可中断前一条修改命令。遇到 `REV_CONFLICT` 先读取最新状态。v1.1.2 及更早版本若返回 `REARM_REQUIRED`，表示参数已写入且主输出已关闭。

SX150C 串口为 115200 波特时，有线可选 30 Hz，按约 33.33 ms 推送。从 v1.1.8 起，BLE 协商的 ATT MTU 达 247 时也可选 30 Hz。MTU 为 100～246 时最快 200 ms，低于 100 时为 2500 ms。SX150C 串口为 9600 波特时，推送间隔至少 200 ms。

常规推送到期时，如果当前读数和状态版本都已发送，则等待 SX150C 返回新读数。故障状态和开关变化仍可推送。客户端按 `measured.sampled_ms` 统计新读数。有线 921600、8N1 可传约 92,160 字节/秒。按 465 字节一帧估算，30 Hz 档约占 15% 串口带宽。实机连续发送长帧时发现漏字节，固件每次最多写入 32 字节，两次写入间隔至少 0.9 ms，客户端按换行组帧。使用旧固件联调时，在 USB 检查脚本加 `--baud 115200`。

### BLE 断线恢复

固件在 NimBLE 主机任务复核连接句柄，未完成的断开会限频重试；若底层已无该连接，不再等待应用断开回调。设备空闲时，如果未广播，固件会尝试恢复广播，不会打断有效连接、清除配对或重启电源。USB 固件升级和清除配对期间不执行额外的广播恢复。

UART0 的 `pm-ble` 日志用于区分断线阶段：`GAP disconnect` 记录底层原因，`terminate` 记录固件主动断开原因及返回值，`clearing stale handle` 表示底层连接已经不存在。主动断开原因依次为：1 认证拒绝、2 接收队列满、3 认证超时、4 应答队列满、5 应答发送失败、6 应答确认超时、7 应答指示未订阅。

### BLE 分片

服务 UUID 为 `4d505301-7e64-4d4a-9f80-021a56c11000`。五个特征复用此前缀，末两位对应下表。连接并完成系统绑定后订阅 RSP，可同时订阅 STATE。

| 末两位 | 特征 | 操作 |
| --- | --- | --- |
| 01 | CMD | Write with Response，上传命令分片 |
| 02 | RSP | Indicate，返回响应分片 |
| 03 | STATE | Notify，推送状态分片 |
| 04 | INFO | Read，直接读取设备与协议概要 JSON |
| 05 | HEARTBEAT | Write with Response，写入 4 字节小端 `client_id` |

分片头为 8 字节，依次是版本 `1`、标志 `0`、小端 uint16 消息号、从 0 开始的序号、总片数、小端 uint16 完整消息长度。每片载荷最多为 `min(ATT_MTU−3,512)−8`，MTU 23 时为 12 字节。除末片外每片载荷相同，完整消息含 LF。CMD 的同一消息按序在 5 秒内发完。HEARTBEAT 可插在 CMD 分片之间，控制动作以 RSP 读回结果为准。

## 验证

本地构建目标为 ESP32 Dev Module。模拟 SX150C 串口验证直接控制、数据组切换、OVP/OCP/OPP 写入与读回、75 W 边界、指令重复、CRC 和 MTU 23/247 分包。v1.2.1 验证 USB 和 BLE 主动断开、BLE 掉线及重连后保留主输出与继电器状态，同时验证了显式关断和 SX 串口失联保护。2026-11-19 使用 COM3 烧录并校验 v1.1.0，通过 COM4 实机验证直接写入和调出 M0～M10。更新到 v1.1.1 后，用新版客户端将 M1 的 OPP 从 160.0 W 写为 75.0 W，读回一致且「开启输出」按钮可用。主输出在联调期间保持关闭。[硬件验证项目](../docs/Type-C可调直流电源详细设计书.md#13-硬件确认与验证)
