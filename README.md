# VoltageAir · 串口实时监测

> 串口气压（mmHg）/ 电压（V）实时波形显示 + 循环统计工具

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![PyQt5](https://img.shields.io/badge/PyQt5-5.x-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

---

## 功能

- **实时采集**：串口读取气压（mmHg）、电压（V），逐行解析
- **双 Y 轴波形**：气压（橙）/ 电压（蓝）共图显示
- **完整交互**：
  - 滚轮缩放、拖拽平移、右键框选放大
  - 鼠标悬停显示 `(x, 气压, 电压)` 数值
  - 快捷键：`+` / `-` 缩放，`0` 重置，`F` 贴合数据，`G` 网格，`空格` 暂停
  - 跟随最新 / 锁定视图 / 自适应一键切换
- **轮次统计**：轮次、电压、峰值、充气时间、泄气时间、状态
- **日志功能**：
  - 串口数据自动落盘（`logs/YYYY-MM-DD/raw_*.log`）
  - 支持加载历史日志回放（`.txt` / `.log` / `.dat`）
  - 一键导出统计 CSV
- **程序图标**：每次启动动态生成 PNG；打包时使用 `app.ico`

---
<img width="1439" height="962" alt="image" src="https://github.com/user-attachments/assets/f573dfed-89c2-42b9-938f-d929fcd0d704" />


## 快速开始

### 环境要求

- Python 3.8+
- Windows / macOS / Linux

### 安装依赖

```bash
pip install PyQt5 pyserial numpy matplotlib pillow
```

### 运行

```bash
python main.py
```

---

## 使用说明

### 连接串口

1. 选择 **PORT**（插拔设备后点“刷新”）
2. 选择 **BAUD**（默认 115200，可切换至 921600）
3. 点 **连接**

> X 轴 0 点自动对齐到**本轮测试起点**（`===== Auto BP Test START =====` 或
> `[Test] inflate START, target=220 mmHg`），而不是“点击连接”的时刻。

### 加载日志回放

1. 点 **加载日志**，选择 `.txt` / `.log` / `.dat` 文件
2. 状态栏显示进度 `加载中… 已读 / 总行数`
3. 加载完成后自动填入波形、表格、统计

### 保存统计

- 点 **保存统计** → 保存为 `round_stats.csv`
- 文件带 BOM（`utf-8-sig`），Excel 打开不乱码

### 清空

- **清空** —— 波形、表格、统计归零；串口继续收数据，X 轴从下一个测试起点重新对齐
- **清空打印** —— 仅清空下方“串口打印”面板，不影响波形

---

## 目录结构

| 文件 | 作用 |
|---|---|
| `main.py` | 启动入口，高 DPI 支持、图标生成、延迟导入主窗口 |
| `app.py` | 主窗口：UI 布局、串口/日志调度、自适应缩放 |
| `parser.py` | 单行解析：时间戳、压力、电压、事件 |
| `stats.py` | 轮次统计：峰值、充气/泄气时间、状态 |
| `plot_panel.py` | matplotlib 双 Y 轴绘图面板（含降采样、交互） |
| `serial_worker.py` | 串口读取线程（后台阻塞读，逐行发信号） |
| `log_loader.py` | 日志回放线程（“虚拟串口”） |
| `log_writer.py` | 原始日志落盘（按日期分目录、防重名） |
| `icon_gen.py` | 程序图标生成（PNG / ICO） |
| `theme.py` | 颜色 Token + 动态 QSS |
| `diag_plot.py` | pyqtgraph 诊断脚本（排查绘图尺寸问题） |
| `VoltageAir.spec` | PyInstaller 打包配置（onedir 多文件模式） |
| `app.ico` | exe / 窗口图标 |

---

## 打包为 exe

### 安装 PyInstaller

```bash
pip install pyinstaller
```

### 一键打包（多文件模式）

```bash
pyinstaller --clean --noconfirm VoltageAir.spec
```

产物在 `dist/VoltageAir/`，**整个目录一起拷**给别人就能跑（PyInstaller 6.x 依赖在 `_internal/` 子目录里）。

### 重新生成 spec

```bash
pyinstaller ^
  --name VoltageAir ^
  --icon app.ico ^
  --windowed ^
  --noconfirm ^
  --clean ^
  --collect-data matplotlib ^
  --collect-submodules serial ^
  --collect-submodules matplotlib.backends ^
  --hidden-import PyQt5.sip ^
  --hidden-import PIL ^
  --hidden-import PIL.Image ^
  --hidden-import PIL.ImageDraw ^
  --exclude-module tkinter ^
  --exclude-module PyQt5.QtWebEngineWidgets ^
  main.py
```

---

## 数据格式

### 串口原始行（示例）

```text
[15:31:57.088] [Test] inflate START, target=220 mmHg
[15:31:58.017] [Test] inflating: 39 mmHg
[15:32:06.475] [Test] reached 220 mmHg, stop inflate
[15:32:06.475] [Test] slow deflate START
[15:32:07.397] [Test] deflating: 206 mmHg
[15:32:15.637] [Test] reached 40 mmHg, slow deflate END
[15:32:15.637] [Test] === Test #1 DONE, status=1, total=1 ===
[15:32:15.637] [Round DONE] bat=0.424 V level=0 | Date=2024-01-01 00:00:29 Monday
```

### 解析规则（`parser.py`）

| 日志行 | 用途 |
|---|---|
| `[Test] inflating: 57 mmHg` | 压力值 |
| `[Test] deflating: 214 mmHg` | 压力值 |
| `[Round DONE] bat=0.424 V` | 电压值 |
| `===== Auto BP Test START =====` | 轮次起点 |
| `[Test] inflate START, target=220` | 充气起点 |
| `[Test] slow deflate START` | 泄气起点 |
| `[Test] reached 220 mmHg, stop inflate` | 充气终点 |
| `[Test] reached 40 mmHg, slow deflate END` | 泄气终点 |
| `Test #1 DONE, status=1, total=1` | 轮次状态 |
| `[AutoTest] cool down 60s...` | 冷却时长 |

**统计口径**

- 充气时间 = `reached N, stop inflate` 时刻 − `inflate START` 时刻
- 泄气时间 = `reached N, slow deflate END` 时刻 − `slow deflate START` 时刻
- 状态映射：`status=1 → OK`，其他 → `FAIL(n)`

---

## 常见问题

| 症状 | 原因 | 解决 |
|---|---|---|
| 串口下拉为空 | 缺 `serial.tools` | `pip install pyserial` |
| 打包后闪退 | 缺 Qt 平台插件 | spec 里 `collect_data_files('PyQt5')` |
| 绘图面板空白 | 缺 matplotlib 后端 | spec 里 `collect_submodules('matplotlib.backends')` |
| 字体显示为方框 | 缺 mpl-data 字体 | spec 里 `collect_data_files('matplotlib')` |
| Excel 打开 CSV 乱码 | 编码问题 | 已写 BOM（`utf-8-sig`），无需处理 |
| 波形不随时间前进 | 未识别到测试起点 | 检查日志里是否有 `Auto BP Test START` 或 `inflate START` |

---

## 开发

### 调试串口数据

不接硬件时，直接加载示例日志：

```bash
python main.py
# 点“加载日志”，选 实例/*.TXT
```

### 排查绘图尺寸

```bash
python diag_plot.py
```

会在 2.5 秒后打印窗口、绘图面板、ViewBox 的实际尺寸，用于定位布局问题。

---

## License

MIT
