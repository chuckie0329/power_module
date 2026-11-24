# PowerModule 蓝牙配对恢复：私测候选

- 基线：`df152e9b1f73d2d7cbb4bd79fa6a2e33e8c2106d`。
- 需要 ESP32 固件候选 **1.2.3** 和客户端候选 **1.0.2** 搭配。两者均未发布到 GitHub 稳定通道。现有稳定客户端 1.0.1 可用本地文件升级 ESP32，但没有清除按钮。
- `PowerModule-ESP32-1.2.3-Bond-Recovery-TEST.bin` 是 4 MB ESP32-WROOM-32E 默认 OTA 分区的应用镜像，不是全闪存镜像。请不要当作分区表、bootloader 或全量烧录文件。
- 安装 OTA 前确认负载安全、主输出和两路继电器都已经关闭，在现有客户端通过 USB 的「升级 → 选择本地固件」选择候选 BIN，等待设备重启且确认版本 1.2.3，期间保持供电、USB 连接。个别设备版本不兼容时停止，不要用擦除 NVS/整机方式绕过。
- 仅在 USB 连接上操作「清除蓝牙配对」。先关闭设备所有输出并确认读回，关闭已连接的蓝牙客户端。弹窗应显示当前设备 ID，并说明以前所有电脑都需重新配对；取消不发命令，确认后界面应显示清除数量或明确失败。
- 在新电脑 Windows「蓝牙和设备 → 设备」中移除旧 PM-xxxx 条目（如果有），重新添加设备，使用项目原有配对 PIN；然后在客户端使用 BLE 扫描并连接。实机验证时保持输出关闭，先验证连接和遥测，不用上电负载验证。
- 此功能不重启设备，不改变输出、继电器、固定 PIN、加密/认证要求、电源配置或 USB。仅暂时暂停已有广告，以便 NimBLE 逐条解绑并更新解析列表；会尝试恢复原有广告状态。旧设备没有该能力，按钮不可用。

## 在 Windows 构建客户端

用 64 位 Python 3.12，进入解压后的目录，在 PowerShell 运行：

```powershell
py -3.12 -m pip install -r pc_client/requirements.txt pyinstaller==6.16.0
.\tools\build_pc_windows.ps1
```

按官方项目相同方式构建 `pc_client/build/PowerModule-PC.exe`，保留原应用图标、Qt 与 BLE。也可以用 `py -3.12 -m pc_client` 运行源码版；若要在 Windows 验证发布形态，优先用原生方式打包。私测候选未在原生 Windows 上运行。

## 可复跑检查

```sh
g++ -std=c++17 -Wall -Wextra -Ifirmware/PowerModule tools/tests/bond_recovery.cpp -o /tmp/pm-bond-test
/tmp/pm-bond-test
QT_QPA_PLATFORM=offscreen python -m unittest discover -s pc_client/tests -q
arduino-cli compile --profile power_module --build-path .work/esp32-build firmware/PowerModule
```
