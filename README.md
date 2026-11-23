# Type-C 可调直流电源

这是一套由 USB Type-C PD 供电的可调直流电源。PD 20 V 同时供电给 SX150C 和 ESP32 双路继电器板。ESP32 经 HW-221 电平转换与 SX150C 通信，Windows 客户端可通过 CH340E 有线串口或 BLE 控制主输出及两路继电器。整机输入预算为 100 W，初始输出设定按 75 W 目标验证。

![Type-C 可调直流电源整机外观渲染图](docs/images/power-module-render.png)

## 下载和使用

1. 下载 Windows 便携客户端和配套固件。`.bin` 用于客户端升级，`.hex` 用于 ESP32 下载口烧录。 [发布下载](https://github.com/chuckie0329/power_module/releases/latest)
2. 按接线表连接模块，核对 SX150C 的地址、通讯波特率及 M0 保护设置。 [硬件接线表](docs/简化硬件接线表.md) · [固件联调说明](firmware/README.md)
3. 用 Windows 客户端选择 USB 串口或 BLE 连接。可调电压和限流，设定 OVP、OCP、OPP，切换及备注 M0～M10，并记录电压、电流和功率。默认采样为 20 Hz，曲线数据可导出为 CSV。 [客户端使用与构建说明](pc_client/README.md)

## 项目目录

| 目录 | 内容 |
| --- | --- |
| `docs/` | [电子设计书](docs/Type-C可调直流电源详细设计书.md) · [接线表](docs/简化硬件接线表.md) · [UART 接线](docs/串口模块接线.md) |
| `firmware/` | ESP32 固件、USB 和 BLE 协议、构建与升级说明。 [固件说明](firmware/README.md) |
| `pc_client/` | Windows 客户端 Python 源码及打包说明。 [客户端说明](pc_client/README.md) |
| `modules/` | 模块尺寸、实物图、原厂资料和 PD 网页归档。 [模块资料索引](modules/README.md) |
| `tools/` | PD 网页归档和电子设计资料校验脚本。 [设计资料校验](tools/verify_design.py) |

## 验证资料

在项目根目录运行 `python tools/verify_design.py`，可校验设计书中的 Modbus 示例、PD 归档校验和及资料的本地链接。 [设计验证记录](docs/验证记录.json)

## 许可证

本项目自有的 ESP32 固件与 Windows 客户端源代码采用 MIT 许可证。第三方依赖与厂商资料适用各自的条款。 [MIT 许可证](LICENSE)
