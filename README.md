# PowerModule｜面向 AI Agent 的嵌入式调试电源

**让 AI Agent 不只读日志，也能按键、复位和控制上电。**

我做这个项目，是因为嵌入式联调经常卡在软件之外：Agent 能改代码、看串口日志，但下一步可能是「按住 BOOT，点一下 RESET，再松开 BOOT」。如果每轮测试都得有人守在工作台前，这个流程就没法自动跑完。

PowerModule 把这些物理动作变成可保存、可重跑的时序。它也是一台实用的 Type-C 可调直流电源：由 USB PD 20 V 供电，使用 SX150C 电源模块和 ESP32 双路继电器板。整机输入功率预算为 100 W，初始输出按 75 W 目标验证。

![Type-C 可调直流电源整机外观渲染图](docs/images/power-module-render.png)

[3D 打印模型与打印配置（MakerWorld）](https://makerworld.com.cn/zh/models/3324172-sx150c-type-c-ke-diao-zhi-liu-dian-yuan-wai-ke)

## 主要功能

- **把台架动作编成时序。** 给每一步设置等待时间，独立控制 K1、K2 和主输出。比如把两路继电器接到目标板适合用干接点控制的 BOOT 和 RESET 按键上，就能把「按住 BOOT → 短按 RESET → 松开 BOOT」保存成一套流程，用于重复进入下载模式。也可以编排断电、等待、重新上电，让每轮调试从相同条件开始。时序以 JSON 保存在 EXE 同级的 `sequences/` 目录，下拉选中即载入，点击「运行」才执行。
- **让每轮试验用相同的供电条件。** 通过 USB（CH340E）或蓝牙 BLE 连接，调节电压、限流，分别控制主输出和两路继电器。M0～M10 可保存不同的供电与保护设定，并在客户端添加名称和备注；切组前先关主输出。
- **用曲线读出设备的反应。** 电压、电流、功率分轨显示，共用时间轴。可以滚轮放大上电瞬间，框选区间查看峰值和平均功率，也能导出 CSV 比较多轮测试。
- **调试时不挡代码和终端。** 半透明置顶小窗保留实时读数和常用开关，主窗口可收进系统托盘。遇到连接问题，可通过 USB 导出固件诊断日志；Windows 客户端和 ESP32 固件可分别检查更新。

> 点击「停止」只取消后续动作，不会自动关闭主输出或释放继电器。固件 v1.2.1 起，断开连接也不会自动关断。

## 下一步：把台架接给 AI Agent

**现阶段，时序在 Windows 客户端里编辑和运行。** 接下来计划通过 MCP 工具或 Agent Skill，让 Agent 能调用已保存的时序、读取电源状态和采样结果，再结合目标板的串口日志判断是否进入了预期状态。

最终想打通的是一轮完整的实机联调：**修改代码 → 让板子进入下载模式 → 烧录 → 重新上电 → 读取日志和功耗 → 决定下一轮怎么改。** PowerModule 负责其中必须碰到真实硬件的那几步；MCP 和 Skill 接入是后续计划，目前尚未发布。

## 下载

| 文件 | 用途 |
| --- | --- |
| [Windows 客户端 v1.1.0 · PowerModule-PC.exe](https://github.com/chuckie0329/power_module/releases/download/pc-v1.1.0/PowerModule-PC.exe) | Windows x64 便携版 |
| [ESP32 固件 v1.2.6 · PowerModule-ESP32.bin](https://github.com/chuckie0329/power_module/releases/download/fw-v1.2.6/PowerModule-ESP32.bin) | 通过客户端升级 |
| [ESP32 固件 v1.2.6 · PowerModule-ESP32.hex](https://github.com/chuckie0329/power_module/releases/download/fw-v1.2.6/PowerModule-ESP32.hex) | 通过 UART0 下载口烧录 |

[全部发布](https://github.com/chuckie0329/power_module/releases) · [客户端使用说明](pc_client/README.md) · [固件与烧录说明](firmware/README.md) · [硬件接线表](docs/简化硬件接线表.md)

## 模块与配件采购

以下为 [MakerWorld 外壳项目](https://makerworld.com.cn/zh/models/3324172-sx150c-type-c-ke-diao-zhi-liu-dian-yuan-wai-ke#profileId-3930826) 列出的淘宝原链接。选购时可对照[模块规格与尺寸](modules/README.md)。

| 模块或配件 | 淘宝 |
| --- | --- |
| SX150C 数控升降压电源模块 | [商品链接](https://item.taobao.com/item.htm?id=862021442751) |
| ESP32-WROOM-32E 双路继电器板（5–60 V 供电版） | [商品链接](https://item.taobao.com/item.htm?id=672666360807) |
| Mini PD3.1 Type-C 诱骗模块 | [商品链接](https://item.taobao.com/item.htm?id=1067224413274) |
| HW-221 / TXS0108E 电平转换板 | [商品链接](https://item.taobao.com/item.htm?id=575223989976) |
| 紫色 CH340E Type-C USB 转串口板 | [商品链接](https://item.taobao.com/item.htm?id=967672205574) |
| 红黑香蕉插座 | [商品链接](https://item.taobao.com/item.htm?id=909723612339) |

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
