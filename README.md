# yxmys-刻印快速筛选分解小助手

用于《英雄没有闪》微信小程序刻印界面的视觉辅助工具。程序不会自动遍历刻印列表：启动后由用户点开要处理的刻印，工具按设置完成强化判断、分解确认和统计。

## 功能

- F9 开始、暂停按钮暂停、F10 停止。
- 自动或手动确认分解。
- 可固定强化 1、2 或 3 次。
- 红色词条达到阈值，或强化后没有出现第三种元素颜色时保留并提醒。
- 当前刻印和五种元素统计使用游戏图标显示。
- 异常时在 `logs/` 保存过程报告。

## 项目结构

```text
.
├─ .github/workflows/   GitHub Actions 构建与 Release
├─ config/              默认配置
├─ release/             本地构建产物（不提交到 Git）
├─ src/                 程序代码与图像资源
├─ build.ps1            Windows 单文件 EXE 构建脚本
├─ requirements.txt     Python 依赖
└─ NOTICE.md            作者与版权声明
```

## 本地运行与构建

要求 Windows 和 Python 3.11。核心历史模块来自 Python 3.11 字节码，因此目前不能使用其他 Python 大版本。

```powershell
python -m pip install -r requirements.txt
./build.ps1 -Python python
```

构建结果位于 `release/yxmys-刻印快速筛选分解小助手-v版本号.exe`，是可以单独发布的单文件 EXE，不再依赖旁边的 `_internal` 文件夹。

## GitHub Release

推送普通提交时，GitHub Actions 会提供临时构建产物。推送 `v*` 标签时，会自动创建同名 GitHub Release，并上传单文件 EXE：

```powershell
git tag -a v2.1 -m "v2.1"
git push origin main --tags
```

## 当前源码状态

UI、增强判断覆盖层和错误日志模块已有可读源码。早期识别与控制核心目前仅保留 Python 3.11 `.pyc`，仓库可以构建和运行，但还不是完全可读的开源源码。正式公开前建议逐步将这些历史模块重建为 `.py`，并选择明确的开源许可证。

作者：CyberAn

## 支持作者

如果你觉得这个工具不错，可以请我喝一杯咖啡 ☕

点击下面的收款码图片可以查看原图：

<a href="assets/wechat_pay.jpg"><img src="assets/wechat_pay.jpg" alt="微信收款码" width="320"></a>

