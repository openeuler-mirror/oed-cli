---
name: openeuler-sig-meetings
description: "查询 openEuler 社区 SIG/TC 会议安排与会议纪要的 oed 实战路径（会议列表 listSigMeetings + etherpad 纪要 getText）。任务涉及'查 openEuler 某个 SIG/TC/安全委员会等的例会/会议纪要/议题结论'时加载本 skill。核心坑：① Windows 下 oed 输出中文必挂 GBK UnicodeEncodeError，必须 PYTHONIOENCODING=utf-8 且重定向到文件；② listSigMeetings 带任何排序参数（--order-by/--order-type）会触发后端 500 AttributeError，裸调即可（默认就是日期倒序）；③ Git Bash 的 /tmp 对 Windows 原生 Python 不可见，中间文件一律用 C:/ 路径；④ 会议预约系统的记录可能滞后数月，近期纪要以 etherpad pad 为准。"
---

# openeuler-sig-meetings — SIG/TC 会议与纪要查询实战

用 `oed` CLI 查询 openEuler 社区某 SIG（含 TC 技术委员会）的会议安排与会议纪要。
本 skill 是 [`oed-cli`](../oed-cli/SKILL.md) 的专题补充：oed 通用规则（JSON 输出契约、exit code、`--json`/`--params` 区分等）以 oed-cli skill 为准，这里只记**踩过的坑**和**验证过的最优路径**。

## 数据模型（先懂这个再动手）

两个数据源，分工不同：

| 来源 | 内容 | 权威性 |
|---|---|---|
| `meeting` 服务（`listSigMeetings`） | 会议预约系统的记录：日期、主持、与会人、议题列表、etherpad 链接 | **可能滞后数月**（实测 TC 最新记录 2026-04-23，而实际例会已开到 10 月） |
| `etherpad` 服务（`getText`） | 纪要正文 pad（如 `TC-meetings`），各次例会按时间**倒序追加**在同一 pad 里 | **近期纪要的权威来源**，含结论、遗留问题 |

结论：**查近期内容，etherpad 才是主数据源**；meeting 服务用来拿 etherpad 链接、历史预约记录和与会人名单。

## 最优调用路径（4 步，全部验证通过）

以下 `<SIG>` 用小写群组名（如 `tc`），`<TOPIC>` 是工作主题名（如 `tc`）。

### 第 1 步：建工作目录 + 拉会议记录

```bash
mkdir -p /c/test/_<TOPIC>_work
PYTHONIOENCODING=utf-8 oed meeting listSigMeetings --gn <SIG> > /c/test/_<TOPIC>_work/<SIG>_p1.json 2>&1
```

- **裸调，不带 `--order-by` / `--order-type`**（带了后端必 500，见坑 2）。返回本身就是日期倒序。
- 默认每页 10 条；响应里有 `total`，需要翻页就加 `--page N`（翻页参数安全，排序参数不安全）。

### 第 2 步：从记录中提取 etherpad 链接 → padID

```bash
python -c "
import json
d = json.load(open(r'C:/test/_<TOPIC>_work/<SIG>_p1.json', encoding='utf-8'))
for m in d['response']['data'][:3]:
    print(m['date'], '|', m['topic'], '|', m['etherpad'])
"
```

- padID = etherpad URL 的**最后一段**：`https://etherpad.openeuler.org/p/TC-meetings` → padID 为 `TC-meetings`。
- 多数 SIG 全部例会共用一个 pad（TC 就是），取任意一条记录里的链接即可。

### 第 3 步：拉取 pad 全文（真正的纪要）

```bash
PYTHONIOENCODING=utf-8 oed etherpad getText --pad-id "TC-meetings" > /c/test/_<TOPIC>_work/<SIG>_pad_text.json 2>&1
python -c "
import json
d = json.load(open(r'C:/test/_<TOPIC>_work/<SIG>_pad_text.json', encoding='utf-8'))
text = d['response']['data']['text']          # 注意：文本在 response.data.text
open(r'C:/test/_<TOPIC>_work/<SIG>_pad.txt', 'w', encoding='utf-8').write(text)
"
```

### 第 4 步：定位时间窗口内的会议 → 用 Read 工具读

```bash
python -c "
import re
text = open(r'C:/test/_<TOPIC>_work/<SIG>_pad.txt', encoding='utf-8').read()
seen = []
for d_ in re.findall(r'\d{4}-\d{2}-\d{2}', text):
    if d_ not in seen: seen.append(d_)
print(seen)   # 按出现顺序 = 时间倒序；取 >= 窗口起点的日期段
"
```

然后用 **Read 工具**读 `..._pad.txt`（不要 cat 到控制台，见坑 4），按日期段切分归纳。
pad 内日期行格式是 `Date日期：YYYY-MM-DD`，直接用通用 `\d{4}-\d{2}-\d{2}` 正则扫即可，**别费劲写带中文括号的精确正则**（实践中第一版精确正则没匹配上，通用正则一次命中）。

## 踩坑清单（每一坑都是实跑踩中的）

1. **GBK UnicodeEncodeError 直接崩**
   症状：`oed` 命令本身成功，但在最后 `echo` 输出时抛 `UnicodeEncodeError: 'gbk' codec can't encode ...`，exit 1，什么结果都拿不到。
   原因：Windows 控制台默认 GBK，响应里大量中文/生僻字超出国标码表。
   **修复：每次调 oed 都加 `PYTHONIOENCODING=utf-8` 前缀，并把 stdout 重定向到文件**，不要让它直接打屏。

2. **`listSigMeetings` 带排序参数 → 后端 500**
   症状：exit 3，`response._non_json_body` 里是一整页 Django AttributeError 报错 HTML（`at /meeting/sigmeetings/tc/`）。
   触发：`--order-by date --order-type desc`（与 `--size 50` 组合实测必挂；裸调 `--gn tc` 必成）。
   **修复：永远裸调**，默认已是日期倒序；只允许加 `--page` 翻页。看到 exit 3 + `_non_json_body` HTML = 后端 bug，别重试同一参数组合，改参数。

3. **`/tmp` 对 Windows 原生 Python 不可见**
   症状：bash 里 `> /tmp/x.json` 写入成功，紧接着 `python -c "open('/tmp/x.json')"` 报 FileNotFoundError。
   原因：Git Bash 的 /tmp 是 MSYS 虚拟路径，native Python 不认。
   **修复：中间文件统一放 `C:/test/_<topic>_work/` 这类 Windows 路径**；bash 侧写 `/c/test/...`，Python 侧用 `C:/test/...`。

4. **cat 出来全是乱码（假象）**
   症状：UTF-8 文本 cat 到终端显示乱码，误以为数据坏了。
   原因：又是控制台编码，文件本身是好的。
   **修复：中文内容一律落盘成 `.txt` 后用 Read 工具读**，Read 按文件编码正确解码。

5. **会议预约系统数据滞后 → 别在 meeting 服务里找近期纪要**
   实测：TC 共 56 条预约记录，最新一条 2026-04-23，双周例会从未中断，但 4 月后的都没录入预约系统。
   **修复：近期内容直接进 etherpad**；meeting 服务只当"etherpad 链接 + 历史记录"索引用。

6. **群组名大小写**：查询用小写 `--gn tc` 即可命中，返回记录里 `group_name` 显示为大写 `TC`，无需试错。

## 快速排障对照

| 现象 | exit | 判定 |
|---|---|---|
| `UnicodeEncodeError: 'gbk'` | 1 | 加 `PYTHONIOENCODING=utf-8`（坑 1） |
| `_non_json_body` 是 Django 报错 HTML | 3 | 后端 500，检查是否传了排序参数（坑 2） |
| Python `FileNotFoundError` on /tmp | — | 换 `C:/` 路径（坑 3） |
| `ok:false, status:401/403` | 3 | 需要登录态，走 `oed auth login`（listSigMeetings/getText 均为公开接口，一般遇不到） |

## 验证过的完整实例（2026-11-06，TC 近两月归纳）

```bash
mkdir -p /c/test/_tc_work
PYTHONIOENCODING=utf-8 oed meeting listSigMeetings --gn tc > /c/test/_tc_work/tc_p1.json 2>&1        # total=56
PYTHONIOENCODING=utf-8 oed etherpad getText --pad-id "TC-meetings" > /c/test/_tc_work/tc_pad_text.json 2>&1
# → response.data.text 共 23k 字符，含 2025-08 ~ 2026-11-12 全部例会纪要
# 两月窗口（2026-09-06 起）命中 4 场：09-09、09-23、10-14、10-28 + 11-12 线下预告
```

## 相关接口（备查，未踩坑但相关）

- `oed meeting getMeetingDate` — 全部会议日期索引（可按 date 过滤）
- `oed meeting listMeetings --topic <关键词> --date <日期>` — 全社区会议数据检索
- `oed etherpad getHTML --pad-id <padID>` — 带格式纪要（保留加粗/列表结构）
- `oed etherpad getLastEdited --pad-id <padID>` — 判断 pad 最后更新时间（校验纪要新鲜度）
- 历史纪要归档仓：`https://atomgit.com/openeuler/TC/tree/master/Meeting_Minutes`（更细的逐场纪要文件，经 `oed ag` 访问需先 `oed ag login`）
