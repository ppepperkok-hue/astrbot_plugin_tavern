# SillyTavern 内部机制调研：角色卡 / 世界书 / Prompt 组装 / 聊天记录 / 服务端 API

> 目标：为「AstrBot 插件 + NapCat(QQ) 迁移酒馆前端体验」提供**可照着重写**的格式规范、算法伪代码与 API 事实依据。
> 调研对象：**SillyTavern `release` 分支，`package.json` version = `1.19.0`**，最新提交 `06bde939fb1e9c4c8d8641d810f0a916b5bce127`（2026-09-14，`Fix npm release publishing workflow (#6037)`）。
> 术语/字段名/代码保留英文；结论后附来源；**无法逐字核验的地方一律标 ⚠️**。

---

## 0. 结论速览（先看这 8 条）

1. **酒馆没有"服务端世界书引擎"。** `src/world-info.js` 在 release 分支**已不存在**（`https://api.github.com/repos/SillyTavern/SillyTavern/contents/src/world-info.js?ref=release` 返回 404）；世界书引擎全在浏览器端 `public/scripts/world-info.js`（265,081 B）。同理 Prompt Manager 在 `public/scripts/openai.js`。**服务端只是文件读写 + 上游 provider 代理。**
2. **但酒馆确实有 `POST /api/backends/chat-completions/generate`**（`src/endpoints/backends/chat-completions.js`，1.18.0 镜像中位于第 ~2157 行）。它的 payload 是**已经组装好的 `messages[]`**，服务端只做 provider 格式转换（`convertClaudeMessages` / `convertGooglePrompt` …）与转发。**"酒馆当后端"只能省掉 provider 管道，省不掉 prompt 组装。**
3. 因此"调用已部署酒馆"这条路的**真实价值是**：a) 读取数据（`/api/characters/*`、`/api/worldinfo/*`、`/api/chats/*`）；b) 借用酒馆里已存的 API key 与 provider 适配（`/generate` 支持 `reverse_proxy` + `proxy_password`，可指向任意 OpenAI 兼容端点）。代价是要处理 **session cookie + CSRF token**。
4. **角色卡**：V1 = 6 个必填字段的裸 JSON；V2 = `{spec:"chara_card_v2", spec_version:"2.0", data:{...14 个必填...}}`；V3 = `{spec:"chara_card_v3", spec_version:"3.0", data:{...}}`，新增 `assets / nickname / creator_notes_multilingual / source / group_only_greetings / creation_date / modification_date`；PNG 内嵌走 **tEXt chunk**（keyword `chara`=V2、`ccv3`=V3，值 = base64(UTF-8 JSON)，读取时 **ccv3 优先**）。
5. **世界书**：ST 原生格式 = `{entries: {<uid>: {...}}}`，字段名是**驼峰**（`key`/`keysecondary`/`order`/`disable`…）；角色卡内嵌的 `data.character_book` 则是**下划线**的 V2 lorebook 形态（`keys`/`secondary_keys`/`insertion_order`/`enabled`/`position:"before_char"`），由 `convertWorldInfoToCharacterBook()` 双向映射。
6. **位置枚举是数字**：`before:0, after:1, ANTop:2, ANBottom:3, atDepth:4, EMTop:5, EMBottom:6, outlet:7`；`role` 0=system/1=user/2=assistant ⚠️（0 已确认，1/2 顺序依 ST 惯例，UI 显示为 ⚙️/👤/🤖）。
7. **selectiveLogic 数值与 UI 顺序不同**：`AND_ANY:0, NOT_ALL:1, NOT_ANY:2, AND_ALL:3`。
8. **聊天记录** = `data/<user>/chats/<cardName>/<file>.jsonl`，**第 1 行是 header**（`{user_name, character_name, chat_metadata}`），其余每行一条消息（`name/is_user/send_date/mes/extra`）。群聊在 `data/<user>/group chats/<id>.jsonl`，群元数据在 `data/<user>/groups/<id>.json`。

---

## 1. 调研方法与"可复现抓取配方"（重要：留档给后续接手的人）

本机 PowerShell/curl/git 的 HTTPS 出网已损坏（schannel：`基础连接已经关闭: 接收时发生错误`），Node `fetch` 同样 `fetch failed`。全部证据通过 `web_fetch` / `web_search` 获取。

**已验证可用的纯文本镜像（后续调研请直接用）：**

| 用途 | URL 模板 | 说明 |
|---|---|---|
| ST 源码（1.19.0，release） | `https://raw.gitcode.com/GitHub_Trending/si/SillyTavern/raw/release/<path>` | 纯文本、无 content-type 限制；**部分文件返回 403「暂不支持预览」**（例：`src/server-main.js`、`src/endpoints/openai.js`、`src/endpoints/backends/text-completions.js`、`src/endpoints/backends/chat-completions.js`、`default/content/presets/openai/Default.json`） |
| ST 源码（1.18.0，release 备份镜像） | `https://raw.giteeusercontent.com/mirrors/SillyTavern/raw/release/<path>` | gitcode 403 时用它兜底（版本落后一个小版本） |
| JSON 类文件 | `https://cdn.jsdelivr.net/gh/SillyTavern/SillyTavern@release/<path>` | `.js` 会被工具的 content-type 白名单拒绝（`application/javascript`），`.json`/`.md` 可用 |
| GitHub 元数据 | `https://api.github.com/repos/SillyTavern/SillyTavern/contents/<path>?ref=release`（列目录，**不要**对大文件用，会返回巨大 base64） | 单文件内容用 `.../git/blobs/<sha>`（base64） |
| 官方文档（推荐用 `.md` 源，省 token） | `https://docs.sillytavern.app/<path>.md`（如 `/usage/core-concepts/worldinfo.md`） | `.md` 比 HTML 干净得多 |
| 失败的主机（不要浪费时间） | `raw.githubusercontent.com`（DNS 不解析）、`huggingface.co`、`r.jina.ai`、`rentry.co`、`api.allorigins.win` | — |
| 不稳定 | `deepwiki.com`（持续 429 Vercel checkpoint）、`grep.app/api`（429）、`sourcegraph.com`（403 防火墙） | — |

**关键技巧：`web_fetch` 对大文件只显示"头部 + 尾部"，中段被省略**（例：265 KB 的 `public/scripts/world-info.js` 省略 50,591 B，且这份"完整结果"落盘到 `%TEMP%\dsh-spill-*\session-*\*-web_fetch.txt`）。落盘文件可用 `read`（offset/limit）与 `grep` 工具搜索，**spill 行号 = 源文件行号 + 4**（前 4 行是工具加的头）。所以：**中段函数（如 `checkWorldInfo`）本次无法逐字核验**，列在「情报缺口」里。

---

## 2. 角色卡格式（Character Card V1 / V2 / V3 + PNG + json/yaml）

### 2.1 三种 spec 的判定与必填字段（服务端权威实现）

来源：`src/validator/TavernCardValidator.js`（4,561 B）
<https://github.com/SillyTavern/SillyTavern/blob/release/src/validator/TavernCardValidator.js>

```js
validate() {
    if (this.validateV1()) return 1;
    if (this.validateV2()) return 2;
    if (this.validateV3()) return 3;
    return false;
}
validateV1() { // 全部字段都必须存在（值可为空串）
    const requiredFields = ['name','description','personality','scenario','first_mes','mes_example'];
}
#validateSpecV2()    { return this.card.spec === 'chara_card_v2'; }
#validateSpecVersionV2() { return this.card.spec_version === '2.0'; }
#validateDataV2() {
    const requiredFields = ['name','description','personality','scenario','first_mes','mes_example',
        'creator_notes','system_prompt','post_history_instructions','alternate_greetings',
        'tags','creator','character_version','extensions'];
    // 另要求 Array.isArray(data.alternate_greetings) && Array.isArray(data.tags)
    //        && typeof data.extensions === 'object'
}
#validateCharacterBookV2() { // data.character_book 可选；若存在则必须有 extensions / entries
    const requiredFields = ['extensions','entries'];
    // 且 Array.isArray(characterBook.entries) && typeof characterBook.extensions === 'object'
}
#validateSpecV3()        { return this.card.spec === 'chara_card_v3'; }
#validateSpecVersionV3() { return Number(spec_version) >= 3.0 && Number(spec_version) < 4.0; }
#validateDataV3()        { /* data 只需是 object，V3 必填在 ST 侧被放宽 */ }
```

**注意**：ST 自己的校验对 V3 的 `data` 极宽松（只判类型），**V3 的字段级必填要靠 V3 spec 自己**。

### 2.2 V1（`TavernCard` 裸对象）

来源：`spec_v1.md`（<https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v1.md>，3,038 B），另见 `src/endpoints/characters.js` 的 `importFromJson/importFromPng` 分支。

```ts
type TavernCard = {
  name: string; description: string; personality: string;
  scenario: string; first_mes: string; mes_example: string;
}
```

- 所有字段 mandatory，缺失应为 `""`，**不是 null / undefined**。
- `description / personality / scenario / first_mes / mes_example` **必须**做大小写不敏感宏替换：`{{char}}` 与 `<BOT>` → `name`；`{{user}}` 与 `<USER>` → 应用侧显示名；user 名必须有默认值。
- 内嵌方式：`.json`（不推荐）、PNG/APNG 的 `Chara` EXIF 元数据字段（即 PNG tEXt keyword `chara`，base64 JSON）；WEBP 在 spec 中明确不覆盖。
- `mes_example` 用 `<START>` 分块，`<START>` MAY 被转换成 "Start a new conversation." 之类的 system 消息；示例消息 SHOULD 在上下文不足时被逐块挤出。

**ST 的 V1 兼容现状**：`importFromJson/importFromPng` 检测到 `jsonData.spec === undefined && jsonData.name !== undefined` 时，用 `convertToV2()` 转成 V2 再存储（`src/endpoints/characters.js`）。ST 内部还残留 V1 时代字段：`creatorcomment`（= V2 `creator_notes`）、`talkativeness`（默认 `0.5`）、`fav`、`chat`、`avatar`。

### 2.3 V2（`chara_card_v2`）

来源：`TavernCardValidator.js`（字段清单）＋ `src/endpoints/characters.js` 的 `charaFormatData()`（写入时构造）。

```
{
  "spec": "chara_card_v2",
  "spec_version": "2.0",
  "data": {
    "name": "",                      // 必填
    "description": "",               // 必填
    "personality": "",               // 必填
    "scenario": "",                  // 必填
    "first_mes": "",                 // 必填
    "mes_example": "",               // 必填
    "creator_notes": "",             // 必填（可为 ""）
    "system_prompt": "",             // 必填；"Prefer Char. Prompt" 开启时覆盖 Main Prompt
    "post_history_instructions": "", // 必填；支持 {{original}} 宏
    "alternate_greetings": [],       // 必填，必须是数组（字符串会被包装成 [s]）
    "tags": [],                      // 必填，必须是数组
    "creator": "",                   // 必填
    "character_version": "",         // 必填
    "extensions": {},                // 必填，任意对象
    "character_book": {              // 可选（V2 规范内的 lorebook）
      "name": "<world name>",
      "entries": [ /* 见 3.3 */ ]
    }
  },
  // ⚠️ ST 会在顶层额外挂这些非 spec 字段（导出时下划线开头的私有字段会被清理）：
  "name": "...", "description": "...", "personality": "...", "scenario": "...",
  "first_mes": "...", "mes_example": "...", "creatorcomment": "...",
  "avatar": "none", "chat": "<name> - <humanizedDateTime>",
  "talkativeness": 0.5, "fav": false, "tags": [], "create_date": "<ISO8601>",
  "json_data": "<原始 JSON 字符串>"
}
```

`charaFormatData()` 里 ST 特有的 `data.extensions` 约定（**兼容时必读**）：

```js
_.set(char, 'data.extensions.talkativeness', data.talkativeness || 0.5);
_.set(char, 'data.extensions.fav', data.fav == 'true');
_.set(char, 'data.extensions.world', data.world || '');           // 绑定的世界书名
// character depth prompt（"角色注释"，等价于 depth prompt）
const depth_default = 4; const role_default = 'system';
_.set(char, 'data.extensions.depth_prompt.prompt', data.depth_prompt_prompt ?? '');
_.set(char, 'data.extensions.depth_prompt.depth', depth_value);   // 默认 4
_.set(char, 'data.extensions.depth_prompt.role', role_value);     // 默认 'system'
```

`readFromV2()` 反向映射（**V1 字段由 `data` 回填**，并会对不一致打 warning）：

```
name←data.name, description←data.description, personality←data.personality,
scenario←data.scenario, first_mes←data.first_mes, mes_example←data.mes_example,
talkativeness←extensions.talkativeness(默认 0.5), fav←extensions.fav(默认 false), tags←tags
```

### 2.4 V3（`chara_card_v3`）

来源（三重交叉验证）：
- ST 侧：`TavernCardValidator.js`（`spec === 'chara_card_v3'`、`3.0 <= spec_version < 4.0`）、`src/character-card-parser.js`（写 PNG 时把 V2 数据升级并写入 `spec:"chara_card_v3", spec_version:"3.0"`）、`src/charx.js`（`data.assets[]`）。
- 规范侧：`kwaroran/character-card-spec-v3` — `SPEC_V3.md`（48,247 B）、`concepts.md`（9,851 B）、`README.md`（955 B）
  <https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md>
  README 明确：*"It is based on the CCv2 specification and extends it with new features."*
- 独立实现侧（**字段级细节以此为准**）：Rust crate `chara_card` v0.4.1
  <https://docs.rs/chara_card/latest/chara_card/raw/v3/struct.CharacterCardData.html>

```rust
// v3 相对 v2 的**新增** data 字段（crate 原文，类型即默认/可空性）
pub struct CharacterCardData {
    pub assets: Vec<Asset>,
    pub nickname: Option<String>,
    pub creator_notes_multilingual: HashMap<Language, String>,
    pub source: Vec<String>,
    pub group_only_greetings: Vec<String>,
    pub creation_date: Option<Timestamp>,
    pub modification_date: Option<Timestamp>,
}
```

语义要点（crate 文档原文摘要）：

| 字段 | 语义 / 注意 |
|---|---|
| `assets` | 资源数组。**`name` 不保证唯一**（RisuAI 允许重名），不能用它当 key。条目字段见下。 |
| `nickname` | 角色显示名替换值：`{{char}}` / `<char>` / `<bot>` 应替换为它；但 `name` 仍应作为角色卡的标识符。 |
| `creator_notes_multilingual` | 多语言 creator notes；非空时 `creator_notes` 应视为 `en`。key 是 ISO 语言（crate 用 `isolang::Language`）。 |
| `source` | 引用 ID/URL 数组；**规范未定义语义**，不应依赖跨应用一致；非 URL 来源不应允许用户新增。 |
| `group_only_greetings` | **仅群聊**可用的额外开场白；若不空，实现应把它当作群聊开场白的**唯一来源**（取代普通 greeting）。 |
| `creation_date` | UNIX 时间戳（**秒**级；实现可能写毫秒，需归一化）。⚠️ 你提到的 `source` 之外的 `creation_date` 即此字段。 |
| `modification_date` | 修改时间戳（**注意：任务描述里没提到这个，但 V3 实际有**）。 |

`Asset`（V3 规范条目，ST 侧逐字使用字段名 `type` / `name` / `ext` / `uri`）：

```js
// src/charx.js
collectCharXAssets(card) {
    const assets = _.get(card, 'data.assets');
    return assets.map((asset, index) => {
        const zipPath = this.getEmbeddedZipPathFromUri(asset.uri);  // 'embeded://' | 'embedded://' | '__asset:'
        const ext   = this.deriveCharXAssetExtension(asset.ext, zipPath);
        const type  = String(asset.type).toLowerCase();              // 'icon' | 'user_icon' | 'emotion' | 'expression' | 'background' | ...
        const name  = String(asset.name);
        return { type, name, ext, zipPath, order: index };
    });
}
// 图标：优先 type==='icon' 且 name.toLowerCase()==='main'，否则第一个 icon
// 表情/立绘：type ∈ {emotion, expression} → 存为 sprite；type==='background' → 背景；其余 → misc
```

- **URI 前缀**：`embeded://`（**故意保留 RisuAI 的拼写错误**）、`embedded://`、`__asset:`，其余视为外部 URL。
- 支持的图片扩展：`png jpg jpeg webp gif apng avif bmp jfif`。
- **CharX 容器** = ZIP（可带 SFX 前缀，ST 用 `PK\x03\x04` 定位 ZIP 起点），内含 `card.json`（必须是 V2/V3 spec 卡）+ 资源文件；ST 导入后把 sprite/背景/杂项落盘到 `characters/<folder>/`、`characters/<folder>/backgrounds/`、`user/images/<folder>/`。
- ⚠️ `SPEC_V3.md` 全文（48 KB）本次未逐字获取（`raw.githubusercontent.com` 不可达，base64 渠道 token 成本过高）。**未在本文中出现的 V3 字段（若有）属于情报缺口**（见 §9）。

### 2.5 PNG 内嵌元数据：怎么读、怎么写

来源：`src/character-card-parser.js`（3,284 B）
<https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js>

```js
export const write = (image, data) => {
    const chunks = extract(new Uint8Array(image));
    // 1) 删除所有已存在的 keyword 为 chara / ccv3 的 tEXt chunk
    for (const tEXtChunk of chunks.filter(c => c.name === 'tEXt')) {
        const d = PNGtext.decode(tEXtChunk.data);
        if (d.keyword.toLowerCase() === 'chara' || d.keyword.toLowerCase() === 'ccv3')
            chunks.splice(chunks.indexOf(tEXtChunk), 1);
    }
    // 2) 在 IEND 前插入 chara（V2 数据 base64）
    const base64EncodedData = Buffer.from(data, 'utf8').toString('base64');
    chunks.splice(-1, 0, PNGtext.encode('chara', base64EncodedData));
    // 3) 再尝试插入 ccv3（把 V2 顶成 V3 顶部两个字段后 base64）
    try {
        const v3Data = JSON.parse(data);
        v3Data.spec = 'chara_card_v3';
        v3Data.spec_version = '3.0';
        chunks.splice(-1, 0, PNGtext.encode('ccv3', Buffer.from(JSON.stringify(v3Data),'utf8').toString('base64')));
    } catch { /* ignore */ }
    return Buffer.from(encode(chunks));
};

export const read = (image) => {
    const textChunks = extract(new Uint8Array(image))
        .filter(c => c.name === 'tEXt').map(c => PNGtext.decode(c.data));
    if (textChunks.length === 0) throw new Error('No PNG metadata.');
    const ccv3Index = textChunks.findIndex(c => c.keyword.toLowerCase() === 'ccv3');
    if (ccv3Index > -1) return Buffer.from(textChunks[ccv3Index].text, 'base64').toString('utf8'); // ← V3 优先
    const charaIndex = textChunks.findIndex(c => c.keyword.toLowerCase() === 'chara');
    if (charaIndex > -1) return Buffer.from(textChunks[charaIndex].text, 'base64').toString('utf8');
    throw new Error('No PNG metadata.');
};
```

**结论与实现要点**

- ST **只用 `tEXt`**（依赖 `png-chunks-extract` + `png-chunk-text`，见 `package.json` 依赖）。**不写 `iTXt`、不读 `iTXt`** ⚠️（任务描述里提到 iTXt；PNG 标准中 `iTXt` 是 UTF-8 压缩文本，但 ST 的 `read()` 显式 `filter(chunk => chunk.name === 'tEXt')`，因此**其它工具写的 iTXt 卡在 ST 里读不出来**）。若你要做兼容层：**写 `tEXt`；读时同时兼容 `tEXt`/`iTXt`（更宽容，不会有害）**。
- keyword 是 **`chara`（V2）和 `ccv3`（V3）**，大小写不敏感匹配；值是 **base64(UTF-8 JSON 字符串)**，不是直接 JSON。
- 写入顺序：`chara` 先插、`ccv3` 后插（都在 IEND 前）；读取时 **`ccv3` 优先**。
- 写 `ccv3` 时 ST 是拿"当前 JSON"直接改 `spec/spec_version` 再塞进去的 —— 也就是说 ST 产出的 `ccv3` 里**可能没有 V3 新字段**（`assets`/`nickname` 等），只是打了个 V3 标。做兼容层时**不要把 `ccv3` 里有 `spec_version:"3.0"` 当成真有 V3 数据**。
- 服务端入口：`src/endpoints/characters.js` 的 `readCharacterData()`/`writeCharacterData()`（带 memoryCache + diskCache，`perform.security.*`/`performance.useDiskCache` 可控）。所有角色卡在 ST 里**最终都以「PNG + tEXt」形式存储**在 `data/<user>/characters/<name>.png`。

### 2.6 `.json` 与 `.yaml` 角色卡

来源：`src/endpoints/characters.js` → `router.post('/import')` 的 `formatImportFunctions` 表。

```js
const formatImportFunctions = {
    'yaml': importFromYaml, 'yml': importFromYaml,
    'json': importFromJson, 'png': importFromPng,
    'charx': importFromCharX, 'byaf': importFromByaf,
};
```

- **`.json`**：三种分支
  a) `jsonData.spec !== undefined` → V2/V3 卡（`importRisuSprites()` 顺便吃 RisuAI 的立绘，然后 `readFromV2()`）；
  b) `jsonData.name !== undefined` → **V1 卡**；
  c) `jsonData.char_name !== undefined` → Pygmalion/Gradio notepad（`char_persona`/`char_greeting`/`example_dialogue`/`world_scenario`）。
- **`.yaml`/`.yml`**：**只认一个极简 schema**（不是 Character Card YAML 完整实现）：

```js
const yamlData = yaml.parse(fileText);
// 用到：yamlData.name, yamlData.context → description, yamlData.greeting → first_mes
// personality / mes_example / scenario 一律置空
```

- **`.byaf`**：BYAF 格式（`src/byaf.js` 的 `ByafParser`），另有 `tests/byaf-import.test.js`。⚠️ 未展开调研。
- **导出**：`POST /api/characters/export`，`format: 'png' | 'json'`。导出前会 `unsetPrivateFields()`：`fav=false`、`data.extensions.fav=false`、删除 `chat`；PNG 导出走 `read()→mutateJsonString()→write()`。

---

## 3. 世界书（World Info / Lorebook）

### 3.1 顶层结构（ST 原生 `worlds/<name>.json`）

来源：`src/endpoints/worldinfo.js`（5,336 B，**服务端只做文件读写**）
<https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/worldinfo.js>

```js
export function readWorldInfoFile(directories, worldInfoName, allowDummy) {
    const dummyObject = allowDummy ? { entries: {} } : null;
    if (!worldInfoName) return dummyObject;
    const pathToWorldInfo = path.join(directories.worlds, sanitize(`${worldInfoName}.json`));
    if (!fs.existsSync(pathToWorldInfo)) return dummyObject;
    return JSON.parse(fs.readFileSync(pathToWorldInfo, 'utf8'));
}
// /import 校验：if (!('entries' in worldContent)) throw new Error('File must contain a world info entries list');
// /edit  校验：if (!('entries' in request.body.data)) throw ...   写入 JSON.stringify(data, null, 4)
```

**服务端唯一强约束：必须有 `entries`。** 其余字段（`name`、`extensions`、`originalData`…）都是可选/透传：

```
{
  "name": "<显示名>",              // /list 用它做 name，缺失则回退文件名
  "entries": { "<uid>": { …字段表见 3.2… } },
  "extensions": { … },             // /list 会原样返回
  "originalData": { … }            // 导入过的角色内嵌 character_book 原样留存（characters.js 优先用它）
}
```

`/list` 返回 `[{file_id, name, extensions}]`；文件路径 = `data/<user>/worlds/<name>.json`。

### 3.2 ST 原生世界书条目字段表（**驼峰命名**）

字段名与默认值来源（**逐字**）：`src/endpoints/characters.js` → `convertWorldInfoToCharacterBook()`（它把 ST 条目逐字段读出，因此等价于 ST 条目 schema）＋ `public/scripts/world-info.js` 的 typedef/常量。
`convertWorldInfoToCharacterBook` 原文（1.19.0，`src/endpoints/characters.js` 约第 663 行起）：

```js
const originalEntry = {
    id: entry.uid,
    keys: entry.key,
    secondary_keys: entry.keysecondary,
    comment: entry.comment,
    content: entry.content,
    constant: entry.constant,
    selective: entry.selective,
    insertion_order: entry.order,
    enabled: !entry.disable,
    position: entry.position == 0 ? 'before_char' : 'after_char',
    use_regex: true, // ST keys are always regex
    extensions: {
        ...entry.extensions,
        position: entry.position,
        exclude_recursion: entry.excludeRecursion,
        display_index: entry.displayIndex,
        probability: entry.probability ?? null,
        useProbability: entry.useProbability ?? false,
        depth: entry.depth ?? 4,
        selectiveLogic: entry.selectiveLogic ?? 0,
        outlet_name: entry.outletName ?? '',
        group: entry.group ?? '',
        group_override: entry.groupOverride ?? false,
        group_weight: entry.groupWeight ?? null,
        prevent_recursion: entry.preventRecursion ?? false,
        delay_until_recursion: entry.delayUntilRecursion ?? false,
        scan_depth: entry.scanDepth ?? null,
        match_whole_words: entry.matchWholeWords ?? null,
        use_group_scoring: entry.useGroupScoring ?? false,
        case_sensitive: entry.caseSensitive ?? null,
        automation_id: entry.automationId ?? '',
        role: entry.role ?? 0,
        vectorized: entry.vectorized ?? false,
        sticky: entry.sticky ?? null,
        cooldown: entry.cooldown ?? null,
        delay: entry.delay ?? null,
        match_persona_description: entry.matchPersonaDescription ?? false,
        match_character_description: entry.matchCharacterDescription ?? false,
        match_character_personality: entry.matchCharacterPersonality ?? false,
        match_character_depth_prompt: entry.matchCharacterDepthPrompt ?? false,
        match_scenario: entry.matchScenario ?? false,
        match_creator_notes: entry.matchCreatorNotes ?? false,
        triggers: entry.triggers ?? [],
        ignore_budget: entry.ignoreBudget ?? false,
    },
};
```

据此整理成**可直接实现的 ST 原生条目字段表**（`entries[uid]`）：

| ST 字段（驼峰） | 类型 | 默认（ST 侧） | 含义 / 备注 |
|---|---|---|---|
| `uid` | number | 自增/显式 `uid` | 条目在书内的唯一 ID（也用作 `entries` 的 key，ST 允许 key≠uid ⚠️） |
| `key` | string[] | `[]` | 主关键词（旧名 `keys`）。**ST 内部也接受 `keys`**（`addMissingWorldInfoFields` 保证是数组） |
| `keysecondary` | string[] | `[]` | 次关键词（旧名 `secondary_keys`） |
| `comment` | string | `''` | 标题/备忘，**不入 prompt**；截断 `MAX_COMMENT_LENGTH = 100` |
| `content` | string | `''` | 命中后注入的文本 |
| `constant` | boolean | `false` | 蓝灯：不需要关键词，恒激活 |
| `selective` | boolean | `false` | 是否启用次关键词（`keysecondary` 非空且 selective 才做二次判定） |
| `selectiveLogic` | enum | `0` | 0=AND_ANY, 1=NOT_ALL, 2=NOT_ANY, 3=AND_ALL（**注意数值顺序**） |
| `order` | number | `100` ⚠️ | 旧名 `insertion_order`；**越大越靠近上下文末尾**（影响更强） |
| `disable` | boolean | `false` | 旧名 `enabled` **取反**（V2 lorebook 里写 `enabled: !disable`） |
| `position` | enum | `0` | 0=before(↑Char) 1=after(↓Char) 2=ANTop 3=ANBottom 4=atDepth 5=EMTop 6=EMBottom 7=outlet |
| `depth` | number | `4` | atDepth 用；`DEFAULT_DEPTH = 4`；0=最后一条消息之后 |
| `role` | enum | `0` | atDepth 注入的角色：0=system(⚙️) 1=user(👤) 2=assistant(🤖) ⚠️（0 已确认） |
| `probability` | number\|null | `null` | 触发后再次掷骰的百分比（100=必中） |
| `useProbability` | boolean | `false` | 是否启用上一条 |
| `group` | string | `''` | Inclusion Group（逗号分隔可属多组） |
| `groupOverride` | boolean | `false` | "Prioritize Inclusion"：组内按 order 最高者胜 |
| `groupWeight` | number\|null | `null`→`DEFAULT_WEIGHT=100` | 组内随机权重 |
| `useGroupScoring` | boolean | `false` | 组内按命中关键词数（得分）先筛 |
| `scanDepth` | number\|null | `null` | 覆盖全局 scan depth；`MAX_SCAN_DEPTH = 1000` |
| `caseSensitive` | boolean\|null | `null` | 覆盖全局大小写设置 |
| `matchWholeWords` | boolean\|null | `null` | 覆盖全局整词匹配 |
| `automationId` | string | `''` | 与 Quick Reply 命令 ID 匹配则自动执行 |
| `vectorized` | boolean | `false` | 允许被向量检索插入（🔗） |
| `excludeRecursion` | boolean | `false` | "Non-recursable"：不被其它条目激活 |
| `preventRecursion` | boolean | `false` | 激活后不再触发其它条目 |
| `delayUntilRecursion` | boolean | `false` | 仅递归轮次可激活；⚠️ 新版还有"Recursion Level"分级（`delayUntilRecursionLevel`？**字段名未逐字核验**） |
| `sticky` | number\|null | `null` | 触发后保持 N 条消息 |
| `cooldown` | number\|null | `null` | 触发后 N 条消息内不能再触发 |
| `delay` | number\|null | `null` | 聊天消息数 < N 时不激活（0=无限制） |
| `ignoreBudget` | boolean | `false` | 是否跳过 token 预算限制 |
| `outletName` | string | `''` | position=outlet 时的出口名，配合 `{{outlet::Name}}` |
| `triggers` | string[] | `[]` | 允许的生成类型：normal/continue/impersonate/swipe/regenerate/quiet |
| `matchPersonaDescription` 等 6 个 `matchX` | boolean | `false` | 额外匹配源（persona description / char description / personality / depth prompt(角色注释) / scenario / creator notes） |
| `displayIndex` | number | `uid` | 仅编辑器显示顺序 |
| `extensions` | object | `{}` | 保留外来字段（V2 lorebook 转换时原样 `...entry.extensions`） |
| `hash` | number | — | `getStringHash()` 生成的条目指纹，**timed effects 用它匹配条目** |
| `world` | string | — | 运行时注入（书名），`getEntryKey()` = `` `${world}.${uid}` `` |

**全局设置（ST 原生，`public/scripts/world-info.js` 顶部导出变量，逐字）**

```js
export let world_info_depth = 2;                  // scan depth（最近 N 条消息）
export let world_info_min_activations = 0;        // >0 时向后扩展扫描直到激活数达标
export let world_info_min_activations_depth_max = 0;
export let world_info_budget = 25;                // 上下文百分比预算
export let world_info_include_names = true;       // 扫描文本是否带 "Name: " 前缀
export let world_info_recursive = false;
export let world_info_overflow_alert = false;
export let world_info_case_sensitive = false;
export let world_info_match_whole_words = false;
export let world_info_use_group_scoring = false;
export let world_info_character_strategy = world_info_insertion_strategy.character_first;
export let world_info_budget_cap = 0;             // 绝对 token 上限（0=不用）
export let world_info_max_recursion_steps = 0;    // 0=只受预算限制
export const DEFAULT_DEPTH = 4, DEFAULT_WEIGHT = 100, MAX_SCAN_DEPTH = 1000;
export const world_info_insertion_strategy = { evenly: 0, character_first: 1, global_first: 2 };
export const world_info_logic = { AND_ANY: 0, NOT_ALL: 1, NOT_ANY: 2, AND_ALL: 3 };
export const scan_state = { NONE: 0, INITIAL: 1, RECURSION: 2, MIN_ACTIVATIONS: 3 };
export const world_info_position = { before:0, after:1, ANTop:2, ANBottom:3, atDepth:4, EMTop:5, EMBottom:6, outlet:7 };
export const wi_anchor_position = { before: 0, after: 1 };
const KNOWN_DECORATORS = ['@@activate', '@@dont_activate'];
```
（行号见 `public/scripts/world-info.js` 源文件 L73/L77/L855/L866 附近，`#L855`。）

**UI 显示顺序 ≠ 枚举顺序**：左栏下拉里 `AND ANY / NOT ALL / NOT ANY / AND ALL`，但数值是 `0/1/2/3`。

### 3.3 角色卡内嵌的 V2 `character_book`（下划线命名，即"V2 世界书"）

来源：`convertWorldInfoToCharacterBook()`（同 §3.2 引文）＋ V2 spec
<https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md>

```json
{
  "name": "<世界书名>",
  "entries": [
    {
      "id": 0,
      "keys": ["..."],
      "secondary_keys": ["..."],
      "comment": "标题",
      "content": "内容",
      "constant": false,
      "selective": true,
      "insertion_order": 100,
      "enabled": true,
      "position": "before_char",          // 或 "after_char"
      "use_regex": true,                  // ST 写死 true
      "extensions": {
        "position": 0, "exclude_recursion": false, "display_index": 0,
        "probability": null, "useProbability": false, "depth": 4,
        "selectiveLogic": 0, "outlet_name": "", "group": "", "group_override": false,
        "group_weight": null, "prevent_recursion": false, "delay_until_recursion": false,
        "scan_depth": null, "match_whole_words": null, "use_group_scoring": false,
        "case_sensitive": null, "automation_id": "", "role": 0, "vectorized": false,
        "sticky": null, "cooldown": null, "delay": null,
        "match_persona_description": false, "match_character_description": false,
        "match_character_personality": false, "match_character_depth_prompt": false,
        "match_scenario": false, "match_creator_notes": false,
        "triggers": [], "ignore_budget": false
      }
    }
  ]
}
```

⚠️ **未逐字核验**：V2 spec 自身还定义了 `character_book.scan_depth` / `token_budget` / `recursive_scanning` / `extensions`，以及 entry 的 `name` / `priority` / `case_sensitive` 等字段（`spec_v2.md` 本次无法抓取：`raw.githubusercontent.com` DNS 不可达，仓库未上 jsDelivr）。**实现时按"存在即读、缺失不报错"的宽容策略**。

**映射规则（ST ↔ V2 lorebook，逐字）**

| ST 原生 | V2 character_book | 备注 |
|---|---|---|
| `key` | `keys` | — |
| `keysecondary` | `secondary_keys` | — |
| `order` | `insertion_order` | — |
| `disable` | `enabled` | **取反** |
| `position` | `position` | 只区分 before(0)→`before_char` / 其它→`after_char`；真实数值另存 `extensions.position` |
| 其余全部 | `extensions.*` | 保留 ST 语义，避免信息丢失 |

导入方向：`charaFormatData()` 若发现 `file.originalData`（该书本就是从卡里导入的）→ **直接用 `originalData`**；否则 `convertWorldInfoToCharacterBook()`。

---

## 4. 世界书触发算法（可照抄的伪代码）

证据基础：`public/scripts/world-info.js` 的 `WorldInfoBuffer` / `WorldInfoTimedEffects` / `getWorldInfoPrompt`（已逐字读取）＋ 官方文档 <https://docs.sillytavern.xyz/usage/core-concepts/worldinfo/>（HTML）/ `.md`。**`checkWorldInfo()` 函数体本身在本次抓取的中段被省略，标 ⚠️ 的部分是从文档与调用点推定的。**

### 4.1 扫描缓冲区（`WorldInfoBuffer`，逐字逻辑）

```js
// messages 是**逆序**的（messages[0] 是最新一条），见 getWorldInfoPrompt 的 JSDoc: "in reverse order"
class WorldInfoBuffer {
    constructor(messages, globalScanData) { this.#initDepthBuffer(messages); this.#globalScanData = globalScanData; }
    #initDepthBuffer(messages) {
        for (let depth = 0; depth < MAX_SCAN_DEPTH; depth++) {
            if (messages[depth]) this.#depthBuffer[depth] = messages[depth].trim();
            if (depth === messages.length - 1) break;
        }
    }
    get(entry, scanState) {
        let depth = entry.scanDepth ?? this.getDepth();       // getDepth() = world_info_depth + skew
        if (depth <= this.#startDepth) return '';
        if (depth < 0) { console.error(...); return ''; }
        if (depth > MAX_SCAN_DEPTH) depth = MAX_SCAN_DEPTH;
        const MATCHER = '\x01';                                // U+0001 分隔符，可写正则 /\x01{{user}}:.../
        const JOINER = '\n' + MATCHER;
        let result = MATCHER + this.#depthBuffer.slice(this.#startDepth, depth).join(JOINER);
        // 额外匹配源（每个条目单独开关）
        if (entry.matchPersonaDescription   && globalScanData.personaDescription)   result += JOINER + ...;
        if (entry.matchCharacterDescription && ...) result += JOINER + ...;
        if (entry.matchCharacterPersonality && ...) result += JOINER + ...;
        if (entry.matchCharacterDepthPrompt && ...) result += JOINER + ...;   // 角色注释
        if (entry.matchScenario            && ...) result += JOINER + ...;
        if (entry.matchCreatorNotes        && ...) result += JOINER + ...;
        if (this.#injectBuffer.length > 0) result += JOINER + this.#injectBuffer.join(JOINER);
        // 递归缓冲：min-activations 那轮**不**包含递归产物
        if (this.#recurseBuffer.length > 0 && scanState !== scan_state.MIN_ACTIVATIONS)
            result += JOINER + this.#recurseBuffer.join(JOINER);
        return result;
    }
}
```

要点：
- 扫描文本形如 `\x01<最新消息>\n\x01<第二条>…`；官方文档说明 `Include Names` 打开时每条消息前缀是 `Name: `，且 ST 在消息前插入 `\x01`（v1.12.6+），可用正则 `/\x01{{user}}:[^\x01]*?hello/` 精确匹配"某个角色说的某句话"。
- **`\x01` 是"消息边界"标记**，实现兼容层时应照抄，否则以 `^` 锚定的正则 key 行为会不同。

### 4.2 关键词匹配（`matchKeys`，逐字）

```js
matchKeys(haystack, needle, entry) {
    const keyRegex = parseRegexFromString(needle);          // 形如 /pattern/flags
    if (keyRegex) return keyRegex.test(haystack);           // 正则 key：**忽略**大小写/整词等设置

    haystack = caseSensitive ? haystack : haystack.toLowerCase();
    const transformedString = caseSensitive ? needle : needle.toLowerCase();
    const matchWholeWords = entry.matchWholeWords ?? world_info_match_whole_words;

    if (matchWholeWords) {
        const keyWords = transformedString.split(/\s+/);
        if (keyWords.length > 1) return haystack.includes(transformedString);   // 多词 key：直接 includes
        const regex = new RegExp(`(?:^|\\W)(${escapeRegex(transformedString)})(?:$|\\W)`);
        return regex.test(haystack);                                            // 单词 key：\W 边界
    }
    return haystack.includes(transformedString);
}
```

- `\W` 边界包含标点，所以 `long live the king` 命中 `king`，`not to my liking` 不命中。
- **官方警告**：整词匹配对中日韩（无空格分词）有害，中文条目建议关闭 —— 对应你的 QQ 场景，**默认建议 `matchWholeWords=false`**。
- `use_regex` 字段只是宣告用，实际判定是"能不能 parse 成 `/.../flags`"。

### 4.3 打分与 selectiveLogic（`getScore`，逐字）

```js
if (Array.isArray(entry.key))          for (const k of entry.key)          if (matchKeys(buf,k,entry)) primaryScore++;
if (Array.isArray(entry.keysecondary)) for (const k of entry.keysecondary) if (matchKeys(buf,k,entry)) secondaryScore++;
if (!numberOfPrimaryKeys) return 0;                       // 没有主键 → 0 分（constant 由别处处理）
if (numberOfSecondaryKeys > 0) switch (entry.selectiveLogic) {
    case world_info_logic.AND_ANY:  return primaryScore + secondaryScore;
    case world_info_logic.AND_ALL:  return secondaryScore === numberOfSecondaryKeys ? primaryScore + secondaryScore : primaryScore;
}
return primaryScore;   // NOT_ALL / NOT_ANY 不参与打分（负面逻辑）
```

⚠️ **未被本文件逐字核验**（在省略的中段）但被官方文档明确定义的整体激活判定：

- 主键命中（primary）是激活的必要条件（若 `constant` 则跳过关键词）。
- `selective=true` 且 `keysecondary` 非空时按 `selectiveLogic` 做最终判定：
  - `AND_ANY`（0）：主键命中 **且任意一个** 次键命中；
  - `AND_ALL`（3）：主键命中 **且全部** 次键命中；
  - `NOT_ANY`（2）：主键命中 **且没有任何** 次键命中；
  - `NOT_ALL`（1）：主键命中，但**所有**次键都命中时要**取消**激活（即"全都命中反而别激活"）。
- `decorators`（`@@activate` / `@@dont_activate`）：`KNOWN_DECORATORS` 白名单，用于在 `comment`/内容里强制/禁止激活（`@@dont_activate` 强制不激活）；⚠️ 具体解析位置与作用范围未逐字核验。

### 4.4 完整伪代码（建议实现版；⚠️ 标注处按文档语义实现）

```python
def check_world_info(chat_messages_reversed, max_context_tokens, settings, global_scan_data):
    buf        = WorldInfoBuffer(chat_messages_reversed, global_scan_data)  # depth 0 = 最新
    activated  = {}          # key = f"{world}.{uid}"
    timed      = load_timed_effects(chat_metadata.timedWorldInfo)           # sticky/cooldown/delay
    all_entries = gather_entries()   # global(选中书) + char lore + chat lore + persona lore + 扩展注入
    for e in all_entries: e.hash = string_hash(entry_signature(e))

    def try_activate(e, scan_state):
        if e.disable:                                     return False
        if timed.is_on_cooldown(e) or timed.blocked_by_delay(e):  return False
        if not trigger_matches(e.triggers, scan_state.trigger_type): return False
        if not character_filter_passes(e.characterFilter, current_char): return False
        if e.constant:
            key_hit = True
        else:
            primary = any(match_keys(buf.get(e, scan_state), k, e) for k in e.key)
            if not primary: return False
            if e.selective and len(e.keysecondary) > 0:
                sec  = [match_keys(buf.get(e, scan_state), k, e) for k in e.keysecondary]
                allm, anym = all(sec), any(sec)
                key_hit = {0: anym, 1: not allm, 2: not anym, 3: allm}[e.selectiveLogic]
        if not key_hit: return False
        if e.useProbability and not roll_percent(e.probability):  return False
        return True

    # ── 主扫描（scan_state.INITIAL） ────────────────────────────────
    candidates = [e for e in all_entries if try_activate(e, INITIAL)]
    # 排序：先 constant，其后恒定 order 降序（sortFn = (a,b) => b.order - a.order）
    candidates.sort(key=lambda e: (0 if e.constant else 1, -e.order))
    # 直接由"聊天文本"命中 vs 由"递归内容"命中 → 前者优先（文档：Entries inserted by directly
    # mentioning their keys have higher priority than those mentioned in other entries' contents）
    ...
    # ── 递归（world_info_recursive / max_recursion_steps） ─────────
    if settings.recursive:
        steps = 0
        while True:
            steps += 1
            if settings.max_recursion_steps and steps >= settings.max_recursion_steps: break
            newly = []
            for e in all_entries_not_activated:
                if e.excludeRecursion: continue                    # 不被别人激活
                if e.delayUntilRecursion and scan_state != RECURSION: continue   # 仅递归轮
                if try_activate(e, RECURSION): newly.append(e)
            if not newly: break
            for e in newly:
                buf.addRecurse(e.content)                          # 内容进入下一轮扫描缓冲
                if e.preventRecursion: e.blocks_further_recursion = True
            candidates += newly
    # ── min activations（与 max_recursion_steps 互斥） ─────────────
    if settings.min_activations > 0 and len(candidates) < settings.min_activations:
        while len(candidates) < settings.min_activations and buf.depth < min(settings.min_activations_depth_max or INF, MAX_SCAN_DEPTH):
            buf.advanceScan()                                      # skew++ → 扫描范围向后扩
            candidates += [e for e in all_entries if try_activate(e, MIN_ACTIVATIONS)]
        # 注意：MIN_ACTIVATIONS 轮**不**扫描递归缓冲
    # ── inclusion group ─────────────────────────────────────────
    for group in groups_of(candidates):
        if e.groupOverride: winner = max(group, key=lambda e: e.order)          # Prioritize Inclusion
        elif settings.use_group_scoring:
            best = max(score(e) for e in group); group = [e for e in group if score(e) == best]
            winner = weighted_random(group, weights=[e.groupWeight or 100])
        else: winner = weighted_random(group, weights=[e.groupWeight or 100])
        drop(group - {winner})
    # ── 预算 ────────────────────────────────────────────────────
    budget = settings.budget_cap or (max_context_tokens * settings.budget / 100)     # world_info_budget 默认 25
    used = 0
    for e in candidates_sorted:            # constant 先，再 order 降序
        if e.ignoreBudget or is_timed_sticky(e): keep                # ⚠️ sticky 免预算未逐字核验
        cost = count_tokens(e.content)
        if used + cost > budget: skip(e)                             # 预算耗尽后后面的条目一律丢弃
        used += cost
    # ── 分派到插入位 ─────────────────────────────────────────────
    out = {worldInfoBefore:"", worldInfoAfter:"", EMEntries:[], WIDepthEntries:[],
           ANBeforeEntries:[], ANAfterEntries:[], outletEntries:{}}
    for e in kept:
        match e.position:
            case 0: out.worldInfoBefore  += render(e)                       # ↑Char 前
            case 1: out.worldInfoAfter   += render(e)                       # ↓Char 后
            case 2: out.ANBeforeEntries.append(render(e))                   # AN 顶部
            case 3: out.ANAfterEntries.append(render(e))                    # AN 底部
            case 4: out.WIDepthEntries.append({depth:e.depth, role:e.role, content:render(e)})   # at-depth
            case 5: out.EMEntries.append(("before", render(e)))             # ↑EM
            case 6: out.EMEntries.append(("after",  render(e)))             # ↓EM
            case 7: out.outletEntries[e.outletName].append(render(e))       # 出口
    # ── timed effects 结算（WorldInfoTimedEffects） ────────────────
    for e in kept: timed.on_activated(e, chat_length)     # 写 sticky/cooldown {hash,start,end,protected}
    timed.cleanup(chat_length)                            # 聊天未推进 → 删除非 protected 效果
    return out
```

### 4.5 时间语义（sticky / cooldown / delay，逐字实现 + 文档）

实现（`WorldInfoTimedEffects`，逐字）：

```js
#getEntryTimedEffect(type, entry, isProtected) {
    return { hash: entry.hash, start: this.#chat.length, end: this.#chat.length + Number(entry[type]), protected: !!isProtected };
}
#getEntryKey(entry) { return `${entry.world}.${entry.uid}`; }        // 状态存 chat_metadata.timedWorldInfo[type][key]
// sticky 结束的回调：若条目还有 cooldown，则立刻挂上 cooldown
'onEnded': { 'sticky': (entry) => { if (!entry.cooldown) return;
    const key = ...; const effect = this.#getEntryTimedEffect('cooldown', entry, true);
    chat_metadata.timedWorldInfo.cooldown[key] = effect; this.#buffer.cooldown.push(entry); } }
// 聊天没有前进（最后一条被 swipe/删除）→ 删除非 protected 的效果
if (this.#chat.length <= Number(value.start) && !value.protected) { delete chat_metadata.timedWorldInfo[type][key]; continue; }
// 条目消失（比如换了角色的书）→ 到期后清理
```

文档规则（<https://docs.sillytavern.xyz/usage/core-concepts/worldinfo.md>）：

1. 时间单位是**消息条数**，0 = 无效果。
2. 效果只在激活它的聊天里生效；分支（branch）继承父聊天状态。
3. 聊天不前进时活动效果会被移除（swipe/删除最后一条）。
4. 修改处于计时状态的条目 → 效果被强制移除。
5. 效果已激活期间，反复命中关键词**不会刷新**时长。
6. `sticky=N` 保持 N 条；`cooldown=N` 之后 N 条不能触发（可与 sticky 串联：sticky 结束后进入 cooldown）；`delay=N` 要求聊天至少有 N 条消息（`delay=1` 表示空聊天/无 greeting 时不可激活）。
7. 官方示例（sticky=3, cooldown=2, delay=2）：`M0 delay / M1 激活 / M2-M4 sticky / M5-M6 cooldown / M7 可再次激活`。

### 4.6 其它机制速查

| 机制 | 事实 |
|---|---|
| `scan_depth`（全局 `world_info_depth`，默认 **2**） | 只扫描最近 N 条消息；`0` = 只评估递归条目与 AN |
| `Include Names` | 扫描缓冲里消息前缀为 `Name: ` |
| 预算 | `world_info_budget` 默认 **25（%）**；`world_info_budget_cap` 是绝对 token 上限（0=不用）。先插 `constant`，再 order 大者；**被关键词直接命中的优先于被其它条目内容命中的** |
| `min_activations` / `max_recursion_steps` | **互斥**。min activations：不满足数量就向后扩展扫描（受 Max Depth 与 Budget 限制），且扩扫不检查递归产物。max recursion steps：`1`≈禁用递归，`2`=只递归一次… |
| 递归 | 条目 **content 中出现其它条目的 key** 即触发对方（`addRecurse`） |
| Vector Storage | 只替代"关键词检查"这一步，其余过滤照旧；用 "Query messages" 而非 scan depth；需要扩展启用 |
| Character Filter | `characterFilter: {isExclude, names[], tags[]}`；名字/标签白名单或黑名单 |
| 注入策略 | `world_info_insertion_strategy`：`evenly(0)` 按 order 混排 / `character_first(1)` 角色书在前 / `global_first(2)` 全局在前；默认 `character_first`。文档中辅助来源顺序：**Chat Lore → Persona Lore → Character/Global Lore** |
| AN 关闭时 | 若 Author's Note 的 Insertion Frequency = 0，则 position=ANTop/ANBottom 的世界书条目**被忽略** |
| Outlet | `position=7` 不自动注入，需在 prompt 字段里写 `{{outlet::Name}}`；名字大小写敏感；条目不能嵌套 outlet |

---

## 5. Prompt 组装顺序（Prompt Manager / preset）与预算裁剪

### 5.1 默认 preset 的完整顺序（**逐字**，最强证据）

来源：`default/content/presets/openai/Default.json`（7,759 B）
<https://github.com/SillyTavern/SillyTavern/blob/release/default/content/presets/openai/Default.json>
（gitcode 403，改用 jsDelivr：`https://cdn.jsdelivr.net/gh/SillyTavern/SillyTavern@release/default/content/presets/openai/Default.json`）

```json
"prompt_order": [{ "character_id": 100000, "order": [
  {"identifier":"main","enabled":true},
  {"identifier":"worldInfoBefore","enabled":true},
  {"identifier":"charDescription","enabled":true},
  {"identifier":"charPersonality","enabled":true},
  {"identifier":"scenario","enabled":true},
  {"identifier":"enhanceDefinitions","enabled":false},
  {"identifier":"nsfw","enabled":true},
  {"identifier":"worldInfoAfter","enabled":true},
  {"identifier":"dialogueExamples","enabled":true},
  {"identifier":"chatHistory","enabled":true},
  {"identifier":"jailbreak","enabled":true}
]},
{ "character_id": 100001, "order": [    // ← 群聊/带 persona 的那套，多了 personaDescription
  "main","worldInfoBefore","personaDescription","charDescription","charPersonality",
  "scenario","enhanceDefinitions(false)","nsfw","worldInfoAfter","dialogueExamples","chatHistory","jailbreak"
]}]
```

**默认 prompt 块定义（`prompts[]`，逐字）**

| identifier | name | role | 备注 |
|---|---|---|---|
| `main` | Main Prompt | system | 默认内容 `Write {{char}}'s next reply in a fictional chat between {{char}} and {{user}}.` |
| `nsfw` | Auxiliary Prompt | system | 默认内容为空 |
| `dialogueExamples` | Chat Examples | system | `marker: true`（占位，由示例对话替换） |
| `jailbreak` | Post-History Instructions | system | ⚠️ 名字沿用旧称 `jailbreak`；PHI 默认空 |
| `chatHistory` | Chat History | system | `marker: true`（占位，由历史消息替换） |
| `worldInfoAfter` | World Info (after) | system | `marker: true` |
| `worldInfoBefore` | World Info (before) | system | `marker: true` |
| `enhanceDefinitions` | Enhance Definitions | system | 默认关闭（`enabled:false`）；内容见下 |
| `charDescription` / `charPersonality` / `scenario` / `personaDescription` | 同名 | system | `marker: true` |

关键概念（文档 <https://docs.sillytavern.app/usage/prompts/prompt-manager/>）：
- **列表自上而下 = 发送顺序**；最底下是"最后发给模型的东西"（通常 PHI）。
- 默认 prompt **不能删除，只能 toggle off**；"Pinned = default"清单与上表一致。
- prompt 可设 `role`（System / User / AI Assistant）与 `triggers`（normal/continue/impersonate/swipe/regenerate/quiet）。
- `Position = Relative` 时按拖拽顺序；`Position = In-Chat` + `Depth` 时**无视拖拽顺序**，插进历史里（Depth 0 = 最后一条消息之后，Depth 1 = 倒数第一条之前…）。
- **同 role + 同 depth 的 in-chat 注入按 `Order` 值升序，且分组顺序固定：User → AI Assistant → System。**

### 5.2 Text Completion 路线（Instruct / story string）

- 由 **Advanced Formatting** 面板控制（不是 Prompt Manager）：`story_string` 模板 + System Prompt + `{{#if}}`/`{{trim}}` 等 Handlebars 语法。
- `Default.json` 里 TC 相关的格式模板也存在 Prompt Manager 的 Chat Completion 预设里（`wi_format` = `{0}`、`scenario_format` = `{{scenario}}`、`personality_format` = `{{personality}}`）；文档说明：
  - `{0}` 是 World Info 内容占位；`{{scenario}}`、`{{personality}}` 同理。
- Post-History Instructions（TC 路线）：作为**不可见的 user role 注入**放在最后一行之前；且必须启用 "Enable System Prompt" 才生效。
- 详细 story_string 默认模板未逐字核验（`public/scripts/power-user.js` 过大），⚠️ 列缺口。

### 5.3 Author's Note（AN）

文档：<https://docs.sillytavern.app/usage/characters/authors-note/>

| 项 | 语义 |
|---|---|
| 存放位置 | **按聊天**保存（"Chat-specific Author's Note"，不随新聊天复制）+ "Default Author's Note" 模板 |
| `After Scenario` | 插在角色定义 Scenario 之后 / 无 scenario 时插在角色定义最后一段之后、示例消息之前 |
| `In-chat` + depth | 插进历史：depth 0 = 历史最末尾，depth 4 = 成为历史里第 4 个实体（排在最末 3 条之前） |
| Insertion Frequency | 0 = 永不插入；1 = 每次生成都插；4 = 每第 4 次用户输入才插 |
| 与 WI 的关系 | 世界书 `ANTop(2)`/`ANBottom(3)` 的条目贴在 AN 内容顶部/底部；**AN 频率为 0 时这些条目被忽略** |
| 元数据键 | `chat_metadata.note_prompt / note_interval / note_position / note_role`（来自 `public/scripts/authors-note.js` 的 `metadata_keys` / `NOTE_MODULE_NAME`）⚠️ 键名未逐字核验 |

### 5.4 Token 预算与"上下文裁剪"

| 项 | 事实 / 来源 |
|---|---|
| 上下文上限 | `openai_max_context`（Default.json 默认 **4095**）、生成预留 `openai_max_tokens`（默认 **300**）；`max_context_unlocked` 放开上限 |
| 世界书预算 | `world_info_budget` = 上下文百分比（默认 **25**），`world_info_budget_cap` = 绝对 token 上限 |
| Token 计数 | `public/scripts/tokenizers.js` 的 `getTokenCountAsync()`（世界书预算/条目成本都走它）；服务端有 `src/endpoints/tokenizers.js`（38,869 B）与 `src/tokenizers/` 目录 |
| 示例消息（EM） | 官方：`Example messages ... only kept until chat history fills up the context (optionally these can be forced to be kept in context)`，**按块（`<START>` 之间）逐步挤出** |
| 角色常量 token | "Permanent tokens" = Character Name + Description + Personality + Scenario；`first_mes` 只在开头发一次 |
| 历史裁剪 | ⚠️ 具体裁剪循环在 `public/scripts/openai.js`（本次未抓取，文件过大）；已知行为：从最旧消息开始丢弃、保留最近若干条、`<START>` 块整体处理。**这是实现兼容层时最需要对照 ST 复现的部分** |
| 递归压缩/摘要 | **不是核心功能**：旧对话摘要是扩展（**Summarize**）或 Vector Storage/Data Bank 的职责，核心引擎不做摘要 |

---

## 6. 聊天记录（chat）存储格式

来源：`src/endpoints/chats.js`（44,549 B，已完整读取）、`tests/chat-info.test.js`、`src/endpoints/characters.js`（`/chats` 路由）、官方文档 `chatfilemanagement`。
<https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/chats.js>

### 6.1 目录布局

```
data/<handle>/                       # 每个用户一套（users.js 的 UserDirectoryList）
├── characters/<name>.png            # 角色卡本体（PNG + tEXt chara/ccv3）
├── chats/<cardName>/<file>.jsonl    # cardName = avatar_url 去掉 ".png"
├── group chats/<groupChatId>.jsonl  # 群聊记录
├── groups/<groupId>.json            # 群元数据（含 chats: [...] 列表）
├── worlds/<worldName>.json          # 世界书
└── backups/chat_<backupKey>_<ts>.jsonl
```

- 保存：`POST /api/chats/save` `{avatar_url:"X.png", file_name:"<name>", chat:[...], force?}` → `path.join(directories.chats, cardName, sanitize(file_name + ".jsonl"))`；越界防护 `isPathUnderParent()`。
- 读取：`POST /api/chats/get` `{avatar_url, file_name}` → 返回**解析后的数组**（每行 JSON.parse，坏行丢弃）。
- 群聊读写：`/api/chats/group/get|info|save|delete|import`（`groupChats/<id>.jsonl`）。
- 命名：默认用 `humanizedDateTime()`（"日期 时间"），重命名 `/api/chats/rename`；**checkpoint/branch 以文件名互相引用**，所以改名会断链（文档）。
- 备份：`backupChat()` → `backups/chat_<key>_<timestamp>.jsonl`，`config.yaml` 的 `backups.chat.{enabled,maxTotalBackups,throttleInterval,checkIntegrity}` 控制；非 ASCII 名字会加 8 位 sha256 后缀避免 CJK 全部塌缩成同一个 key（`getBackupKey()`，issue #5780）。

### 6.2 文件内容：**第 1 行是 header**

来源：`tests/chat-info.test.js` 的 `makeChatJsonl()`（逐字）＋ `getChatInfo()` 的实现。

```jsonl
{"user_name":"User","character_name":"Char","chat_metadata":{"note":"meta"}}
{"name":"Char","is_user":false,"mes":"First message","send_date":"2026-01-01T00:00:00.000Z"}
{"name":"User","is_user":true,"mes":"Second message","send_date":"2026-01-02T00:00:00.000Z"}
```

- `getChatInfo()`：**只在 `itemCounter === 0` 时**读第一行的 `chat_metadata`；`chat_items = itemCounter - 1`（总行数减 header）。
- header 里的 `user_name` / `character_name` 在导入器里被写成 `'unused'`（见下方 import 函数），真实聊天里是实际名字 ⚠️（未逐字核验真实文件）。
- `chat_metadata.integrity` 是**完整性校验 slug**：保存前先比对文件第一行的 `chat_metadata.integrity`，不匹配则抛 `IntegrityMismatchError` → HTTP 400 `{error:'integrity'}`（`checkChatIntegrity()`）。这是防"另一个客户端覆盖了我的聊天"的机制。

### 6.3 消息行字段表

| 字段 | 类型 | 说明 / 证据 |
|---|---|---|
| `name` | string | 发言人显示名（单聊=角色名或 user 名；群聊=具体角色名） |
| `is_user` | boolean | 是否用户消息（**群聊里角色消息都是 false**，靠 `name` 区分是谁） |
| `is_system` | boolean | 可选；系统/hidden 消息，`/export` 的 txt 分支会 `if (data.is_system) return;` 跳过 |
| `send_date` | string \| number | 导入器统一写 `new Date().toISOString()`；⚠️ ST 自己写的可能是时间戳（ms），**读取方两种都要兼容** |
| `mes` | string | 正文。Chub 格式可能是 `{message: "..."}`，导入时被 `flattenChubChat()` 展平 |
| `extra` | object | 附加信息（下表） |

`extra` 中**已核验**存在的键：

| 键 | 证据 |
|---|---|
| `extra.display_text` | `POST /api/chats/export` 的 txt 分支：`(data?.extra?.display_text \|\| data?.mes \|\| '')` |
| `swipes` / `swipe_id` / `swipe_info` | `flattenChubChat()` 处理 `lineData.swipes`；`swipe_id`/`swipe_info` 属于同一族（⚠️ 键名未逐字核验，但 ST 的"swipe"= 同一位置的多条候选回复，必然是数组+索引） |

`extra` 中**社区/生态常见但本次未逐字核验**（⚠️ 写兼容层时按"可选、宽容"处理）：
`api`（生成用的后端）、`model`、`reasoning` / `reasoning_duration`、`gen_started`、`gen_finished`、`token_count`、`inline_image`、`image`、`file`、`title`、`position`、`media`、`variables`。

**给 QQ 迁移的取舍建议**：`mes`/`is_user`/`name`/`send_date` 必留；`extra` 全量**原样透传**（不要删，否则从 ST 往返会丢 swipe/推理/图片元数据）；`swipes` 只在你实现"重roll"时才需要。

### 6.4 `chat_metadata` 字段（逐字 + ⚠️）

| 键 | 状态 | 说明 |
|---|---|---|
| `integrity` | ✅ 逐字 | 完整性 slug（`checkChatIntegrity`） |
| `timedWorldInfo` | ✅ 逐字 | `{sticky:{}, cooldown:{}, delay?}`；条目 key = `<world>.<uid>`，值 `{hash,start,end,protected}`（`WorldInfoTimedEffects`） |
| `note_prompt` / `note_interval` / `note_position` / `note_role` | ⚠️ | Author's Note 的按聊天覆盖（`authors-note.js` 的 `metadata_keys`） |
| `world_info` | ⚠️ | 聊天级 lorebook 覆盖（Chat Lore） |
| `persona` | ⚠️ | 聊天绑定的 persona（描述覆盖） |
| `scenario` / `character_avatar` / `chat_id` / `variables` / `main_chat`（branch 源） | ⚠️ | 生态常见键 |

⚠️ **不存在独立 `.metadata` 文件**：`getChatInfo(pathToFile, ..., withMetadata=true)` 是**从 jsonl 第一行**读 `chat_metadata`，`/api/chats/recent` 与 `/api/characters/chats` 都靠这个开关（`request.body.metadata`）返回元数据。

### 6.5 群聊（group chats）

| 条目 | 事实 |
|---|---|
| 群元数据 | `data/<user>/groups/<groupId>.json`，字段含 `id`、`chats: [chatId,...]`（`/api/chats/search` 与 `/recent` 都靠 `groupData.id` / `groupData.chats` 定位）；`mem`/`name`/`disabled_members` 等字段来自 `src/endpoints/groups.js`（10,782 B）⚠️ 未逐字核验 |
| 群聊天记录 | `data/<user>/groupChats/<chatId>.jsonl`，**格式与单聊 jsonl 完全一致**（同一套 `getChatData` / `trySaveChat`） |
| 发言人 | 每条消息的 `name` = 具体角色名，`is_user` 标记用户 |
| 破坏性迁移 | `migrateGroupChatsMetadataFormat(directories)` 在启动时执行（`src/server-main.js` 调用、`src/endpoints/groups.js` 实现）→ 说明群聊 metadata 格式**改过版**，兼容层要容忍两种布局 |
| 导出 | `/api/chats/export` 支持 `format:'jsonl'`（原样字节返回）与默认 txt（`Name: message\n\n`，跳过 `is_system`） |

### 6.6 聊天导入格式（`POST /api/chats/import`，逐字自动判别）

`body.file_type = 'json' | 'jsonl'`；`json` 时按结构判别，顺序如下：

```js
if (jsonData.savedsettings !== undefined)      importFunc = importKoboldLiteChat;   // Kobold Lite
else if (jsonData.histories !== undefined)      importFunc = importCAIChat;         // CAI Tools
else if (Array.isArray(jsonData.data_visible))  importFunc = importOobaChat;        // oobabooga
else if (Array.isArray(jsonData.messages))      importFunc = importAgnaiChat;       // Agnai
else if (jsonData.type === 'risuChat')          importFunc = importRisuChat;        // RisuAI
// jsonl 分支：第一行必须含 user_name | name | chat_metadata 之一；
// 另外无条件尝试 flattenChubChat() 展平 Chub Chat 格式（mes.message / swipes[].message）
```

---

## 7. 服务端 HTTP API 与"能否仅靠 HTTP 完成一次对话生成"（**决定性结论**）

### 7.1 路由挂载与中间件栈（逐字）

来源：`src/server-startup.js`（19,784 B，**逐字读取**）＋ `src/server-main.js`（17,789 B，经 gitee 1.18.0 镜像**逐字读取**）

```js
// src/server-startup.js → setupPrivateEndpoints(app)
app.use('/', userDataRouter);
app.use('/api/users', usersPrivateRouter);   // 私有用户管理
app.use('/api/users', usersAdminRouter);
app.use('/api/moving-ui', movingUIRouter);
app.use('/api/images', imagesRouter);
app.use('/api/quick-replies', quickRepliesRouter);
app.use('/api/avatars', avatarsRouter);
app.use('/api/themes', themesRouter);
app.use('/api/openai', openAiRouter);
app.use('/api/google', googleRouter);
app.use('/api/anthropic', anthropicRouter);
app.use('/api/tokenizers', tokenizersRouter);
app.use('/api/presets', presetsRouter);
app.use('/api/secrets', secretsRouter);
app.use('/api/extensions', extensionsRouter);
app.use('/api/assets', assetsRouter);
app.use('/api/files', filesRouter);
app.use('/api/characters', charactersRouter);
app.use('/api/chats', chatsRouter);
app.use('/api/groups', groupsRouter);
app.use('/api/worldinfo', worldInfoRouter);
app.use('/api/settings', settingsRouter);
app.use('/api/backgrounds', backgroundsRouter);
app.use('/api/content', contentManagerRouter);
app.use('/api/vector', vectorsRouter);
app.use('/api/translate', translateRouter);
app.use('/api/extra/classify', classifyRouter);   // ⚠️ 另有 /api/classify 未在挂载表内
app.use('/api/extra/caption', captionRouter);
app.use('/api/search', searchRouter);
app.use('/api/backends/text-completions', textCompletionsRouter);
app.use('/api/backends/kobold', koboldRouter);
app.use('/api/backends/chat-completions', chatCompletionsRouter);   // ← 关键
app.use('/api/sd', stableDiffusionRouter);
app.use('/api/horde', hordeRouter);
app.use('/api/speech', speechRouter);
app.use('/api/azure', azureRouter);
app.use('/api/volcengine', volcengineRouter);
app.use('/api/minimax', minimaxRouter);
app.use('/api/data-maid', dataMaidRouter);
app.use('/api/backups', backupsRouter);
app.use('/api/image-metadata', imageMetadataRouter);
```

`redirectDeprecatedEndpoints(app)` 把一堆老路径 308 到新路径（对兼容层很有用，因为它等于一份**路由清单**）：

```
/createcharacter→/api/characters/create, /getcharacters→/api/characters/all,
/getonecharacter→/api/characters/get, /getallchatsofcharacter→/api/characters/chats,
/savechat→/api/chats/save, /getchat→/api/chats/get, /renamechat→/api/chats/rename,
/delchat→/api/chats/delete, /exportchat→/api/chats/export, /importchat→/api/chats/import,
/getgroupchat→/api/chats/group/get, /savegroupchat→/api/chats/group/save,
/getgroups→/api/groups/all, /creategroup→/api/groups/create, /editgroup→/api/groups/edit,
/deletegroup→/api/groups/delete, /getworldinfo→/api/worldinfo/get,
/importworldinfo→/api/worldinfo/import, /editworldinfo→/api/worldinfo/edit,
/getstats→/api/stats/get, /api/content/import→/api/content/importURL, …
```

### 7.2 认证 / CSRF（逐字）

```js
if (cliArgs.listen && cliArgs.basicAuthMode) app.use(basicAuthMiddleware);      // Basic Auth（仅 listen + basicAuthMode）
if (cliArgs.whitelistMode) { const m = await getWhitelistMiddleware(); app.use(m); }
app.use(hostWhitelistMiddleware);                                                // Host 白名单
if (cliArgs.listen) app.use(accessLoggerMiddleware());
app.use(cookieSession({ name: getCookieSessionName(), sameSite:'lax', httpOnly:true,
                        maxAge: getSessionCookieAge(), secret: getCookieSecret(globalThis.DATA_ROOT) }));
app.use(setUserDataMiddleware);

if (!cliArgs.disableCsrf) {
    const csrfSyncProtection = csrfSync({
        getTokenFromState:    (req) => req.session.csrfToken,
        getTokenFromRequest:  (req) => req.headers['x-csrf-token']?.toString(),   // ← 请求头名
        storeTokenInState:    (req, token) => { req.session.csrfToken = token; },
        skipCsrfProtection:   (req) => cliArgs.enableCorsProxy ? /^\/proxy\//.test(req.path) : false,
        size: 32,
    });
    app.get('/csrf-token', (req, res) => res.json({ token: csrfSyncProtection.generateToken(req) }));
    app.use(csrfSyncProtection.csrfSynchronisedProtection);
} else {
    app.get('/csrf-token', (req, res) => res.json({ token: 'disabled' }));
}

// 静态资源 / 登录页
app.get('/', ...); app.get('/login', loginPageMiddleware);
app.use(express.static(path.join(serverDirectory, 'public'), {}));
app.use('/api/users', usersPublicRouter);       // ← 唯一免登录的 /api 分组
// ↓ 以下全部需要登录
app.use(requireLoginMiddleware);
app.post('/api/ping', ...);
app.use(multer({...}).single('avatar'));
app.get('/version', ...);
```

**外部集成必须知道的四件事**
1. **所有 `/api/*`（除 `/api/users` 公开 router）都在 `requireLoginMiddleware` 之后** → 必须带会话 cookie（除 `listen:false` 本机回环等特例）。
2. **CSRF**：先用 `GET /csrf-token` 拿 token，之后**每个写/读请求都要带 `x-csrf-token` 头**；`csrf-sync` 默认对**所有非 GET 请求**生效 → 你会看到"读接口也是 POST"的设计（`/api/characters/all` 是 POST 也是这个原因）。可用 `--disableCsrf`（`npm run start:no-csrf`）或 config 关闭，但会打印安全警告。
3. **Basic Auth 只在 `listen: true` + `basicAuthMode: true` 时挂载**，且是 app 级中间件（对所有路径生效）；它不是 API token 机制。
4. `cookieSession` 是**内存 cookie session**，重启服务会失效。

### 7.3 数据类路由清单（可直接给插件用的）

| 方法 + 路径 | 入参（关键） | 出参 | 来源 |
|---|---|---|---|
| `POST /api/characters/all` | — | 全部卡片（`shallow` 取决于 `performance.lazyLoadCharacters`） | `characters.js` |
| `POST /api/characters/get` | `{avatar_url:"X.png"}` | 单卡（含 `json_data` 原始 JSON 字符串） | `characters.js` |
| `POST /api/characters/chats` | `{avatar_url, simple?, metadata?}` | 该角色的聊天列表（`simple` → `{file_name,file_id}`） | `characters.js` |
| `POST /api/characters/create` / `/edit` / `/edit-avatar` / `/edit-attribute` / `/merge-attributes` / `/rename` / `/duplicate` / `/delete` | 表单/JSON | — | `characters.js` |
| `POST /api/characters/import` | `file_type: png\|json\|yaml\|yml\|charx\|byaf` + multipart | `{file_name}` | `characters.js` |
| `POST /api/characters/export` | `{avatar_url, format:'png'\|'json'}` | 二进制 / JSON | `characters.js` |
| `POST /api/worldinfo/list` | — | `[{file_id,name,extensions}]` | `worldinfo.js` |
| `POST /api/worldinfo/get` | `{name}` | 整本（`{entries:{...}}`） | `worldinfo.js` |
| `POST /api/worldinfo/import` | multipart（或 `convertedData`） | `{name}` | `worldinfo.js` |
| `POST /api/worldinfo/edit` | `{name, data}` | `{ok:true}` | `worldinfo.js` |
| `POST /api/worldinfo/delete` | `{name}` | 200 | `worldinfo.js` |
| `POST /api/chats/get` | `{avatar_url, file_name}` | `[header, msg, ...]` | `chats.js` |
| `POST /api/chats/save` | `{avatar_url, file_name, chat:[...], force?}` | `{ok:true}` / 400 `{error:'integrity'}` | `chats.js` |
| `POST /api/chats/rename` / `/delete` / `/export` / `/import` / `/search` / `/recent` | 见 §6 | — | `chats.js` |
| `POST /api/chats/group/{get,info,save,delete,import}` | `{id, ...}` | — | `chats.js` |
| `POST /api/groups/{all,create,edit,delete}` | — | — | `server-startup.js` 重定向表 ⚠️（`groups.js` 未逐字核验） |
| `POST /api/settings/get` | `{}` | 用户设置（含 `world_names`，`public/scripts/world-info.js` 的 `updateWorldInfoList()` 用它） | `settings.js`⚠️ |
| `POST /api/content/importURL` | `{url}` | 导入角色卡（老路径 `/api/content/import`） | `content-manager.js` |
| `POST /api/backends/chat-completions/status` | `{chat_completion_source, ...}` | 模型列表 | chat-completions.js:1735（1.18.0 行号） |
| `POST /api/backends/chat-completions/bias` | 数组 | token bias | chat-completions.js:2073 |
| **`POST /api/backends/chat-completions/generate`** | 见 §7.4 | provider 响应（或 SSE 流） | chat-completions.js:2157 |
| `POST /api/backends/text-completions/*` | — | — | 文件未抓到 ⚠️ |
| `POST /api/backends/kobold/*` | — | — | ⚠️ |
| `GET /csrf-token`、`GET /version`、`POST /api/ping` | — | — | `server-main.js` |

### 7.4 `/api/backends/chat-completions/generate` 的真实形状（逐字）

```js
router.post('/generate', async function (request, response) {
    if (!request.body) return response.status(400).send({ error: true });

    const postProcessingType = request.body.custom_prompt_post_processing;
    if (Array.isArray(request.body.messages) && postProcessingType) {
        request.body.messages = postProcessPrompt(request.body.messages, postProcessingType, getPromptNames(request));
    }
    if (request.body.json_schema?.value) { /* flattenSchema */ }

    switch (request.body.chat_completion_source) {
        case CHAT_COMPLETION_SOURCES.CLAUDE:     return await sendClaudeRequest(request, response);
        case CHAT_COMPLETION_SOURCES.MAKERSUITE: return await sendMakerSuiteRequest(request, response);
        case CHAT_COMPLETION_SOURCES.MISTRALAI:  return await sendMistralAIRequest(request, response);
        … // PERPLEXITY/GROQ/DEEPSEEK/XAI/… 等
    }
    // 通用 OAI 分支：apiUrl / apiKey / headers / bodyParams 由 chat_completion_source 决定
    if (request.body.chat_completion_source === CHAT_COMPLETION_SOURCES.OPENAI) {
        apiUrl = new URL(request.body.reverse_proxy || API_OPENAI).toString();
        apiKey = request.body.reverse_proxy ? request.body.proxy_password
                                            : readSecret(request.user.directories, SECRET_KEYS.OPENAI, request.body.secret_id);
    }
    … // request.body.messages 直接透传给上游 /chat/completions
});
```

**由此得到的确切结论（这是第 6 问的答案）**

1. ✅ **存在** `/api/backends/chat-completions/generate`（chat-completions.js 1.18.0 第 ~2157 行；1.19.0 文件 sha `0d98c77f7b64502c5b0f504eaa1f67df0f26041e`，大小 126,475 B）。**不存在** `/api/chat/completions` 这种"给你一个已组装好的 prompt"的端点。
2. ❌ **服务端不做 prompt 组装**：`generate` 的入参是 `messages[]`（OpenAI 风格），服务端只做 provider 适配（`convertClaudeMessages` / `convertGooglePrompt` / `convertTextCompletionPrompt` …，来自 `src/prompt-converters.js`）、可选 `custom_prompt_post_processing`、以及把上游响应/SSE 转发回来。**证据**：该 handler 全程只碰 `request.body.*` 与上游 API，**不读 `characters/`、`worlds/`、`chats/` 任何文件**；而世界书引擎（`public/scripts/world-info.js`）与 Prompt Manager（`public/scripts/openai.js`）都在 **`public/`（浏览器端）**。
3. ✅ **可以借道酒馆存好的 key**：`reverse_proxy` + `proxy_password` 两个字段能让调用方把请求指向**任意 OpenAI 兼容端点**（`custom` source 用 `custom_url` + secrets）。也就是说 `/generate` 可以被当成一个"带鉴权的 LLM 代理"。
4. ⚠️ **必须处理 cookie + CSRF**，否则 401/403。并且这是**单用户会话**语义：`readSecret(request.user.directories, …)` 决定用哪个 key，多用户隔离靠 handle。
5. ⚠️ `/api/backends/text-completions/generate`（TC 后端，如 KoboldCpp/text-generation-webui）**大概率同名存在**（同一套前端调用习惯、router 已挂载），但**本次未逐字核验**（gitcode 403、gitee 镜像的该文件也 403）。
6. **结论一句话**：**"酒馆当后端"只适合当"数据源 + provider 代理"。prompt 组装、世界书触发、历史裁剪、宏替换必须由 AstrBot 插件自己实现。**

---

## 8. 结论与路线建议

### 8.1 「把酒馆当后端、由 AstrBot 插件调用」现实吗？

**部分现实 —— 但省不掉最重的活。**

| 能力 | 能否"调用酒馆"得到 | 依据 |
|---|---|---|
| 读角色卡 / 世界书 / 聊天记录 | ✅ 能（`/api/characters/*`、`/api/worldinfo/*`、`/api/chats/*`） | §7.3 |
| 借酒馆的 API key / provider 适配 / 流式转发 | ✅ 能（`/api/backends/chat-completions/generate` + `reverse_proxy`/`proxy_password`/`secret_id`） | §7.4 |
| 让酒馆替你组装 prompt（角色定义+世界书+AN+EM+PHI+历史） | ❌ **不能**（组装在浏览器里） | §7.4（服务端不读任何卡/书/聊天文件） |
| 世界书关键词触发 / selectiveLogic / 递归 / sticky | ❌ 不能（`public/scripts/world-info.js` 是浏览器代码，服务端无对应模块） | §0.1、§4 |
| 上下文预算与裁剪 | ❌ 不能（在 `public/scripts/openai.js`） | §5.4 |
| 多轮"记住上下文" | ❌ 不能（`/generate` 是无状态转发；聊天文件要自己读写或调 `/api/chats/save` 落盘） | §6、§7.4 |
| 鉴权成本 | ⚠️ session cookie + `x-csrf-token`（或 `--disableCsrf`） | §7.2 |

**因此插件必须自己承担**：①Prompt 组装（Chat Completion 默认 order 的那 12 个块）；②世界书引擎（至少关键词/constant/selective/position/depth/order/预算/递归）；③历史管理与裁剪；④宏替换（`{{char}}`/`{{user}}`/`{{original}}`/`{{outlet::}}`/`{{time}}`）；⑤（若要 100% 对齐）timed effects 与 inclusion group。

### 8.2 推荐架构（两条腿走）

**方案 A（推荐，混合）——"ST 当数据源与生成代理，引擎自己实现"**

```
NapCat(QQ) ── AstrBot 插件 ─┬─ 数据层：ST HTTP API（读卡/读世界书/读写聊天；失败回退本地文件缓存）
                             ├─ 引擎层：自带最小 WI 引擎 + CC 默认 prompt order + 历史裁剪（本报告的 §4/§5 伪代码）
                             └─ 生成层：POST /api/backends/chat-completions/generate（或直连 LLM，可切换）
```
优点：拿到酒馆里的卡/书/聊天，不用解析 PNG，不用在两边同步 key；引擎可控、可在 QQ 侧做自己的裁剪策略。
风险：ST 侧升级会动默认 order（要读 `Default.json` 而不是硬编码）；cookie/CSRF 需要运维配置。

**方案 B（备选）——"纯格式兼容层"，完全不依赖 ST 进程**
只做格式：PNG(tEXt chara/ccv3) 读写、V1/V2/V3 归一化、worlds/*.json 解析、`chats/*.jsonl` 读写，然后**直连 LLM**。

### 8.3 最小功能集与工作量估计（1 人日 ≈ 1 名熟练 Python/JS 开发者 1 天）

| 模块 | 必须实现的最小集合 | 人日 |
|---|---|---|
| 角色卡解析 | JSON/YAML/PNG(tEXt `chara`/`ccv3`) 读；V1↔V2↔V3 归一化成内部结构；`{{char}}/{{user}}` 宏；PNG 写回 | **1.5–2.5** |
| 世界书解析 | `entries` 字段全表；V2 `character_book` ↔ 原生双向映射；`originalData` 优先 | **0.5–1** |
| 世界书引擎（最小） | constant / 主键 / selective 4 逻辑 / 大小写 / 整词 / order 排序 / position(0,1,4) + depth + role / 预算(%) / recursion(基础) / 字符过滤器 | **4–6** |
| 世界书引擎（完整） | + timed effects(sticky/cooldown/delay) / inclusion group(权重·优先级·scoring) / min_activations / max_recursion_steps / 递归分级 / outlet / `\x01` 缓冲对齐 / token 计数对齐 | **+4–6** |
| Prompt 组装 | CC 默认 order 12 块 + in-chat 注入(depth/order/role 分组) + AN(位置/频率) + EM 分块与挤出 + PHI/`{{original}}` + macro | **3–4** |
| 上下文预算 | token 计数（用 transformers/tiktoken 近似）+ max_context/max_tokens 预留 + 历史裁剪策略 | **1.5–2** |
| 聊天记录 | `chats/*.jsonl` 读写 + header/`chat_metadata` + integrity + 群聊目录 + 备份 | **1.5–2** |
| NapCat/QQ 适配 | QQ 会话↔(角色,聊天文件) 映射、多用户隔离、长消息分片、限流、指令（切卡/切书/重roll） | **3–5** |
| ST API 对接（方案 A 才需要） | 登录/CSRF、读卡读书读聊天、调 `/generate`、逆向上传聊天 | **1–2** |
| 联调 / 验证 | 与 ST 前端同输入的 prompt diff（用 Prompt Itemization / `/inspect` 之类对照） | **3–5** |
| **MVP（方案 B，最小集）** | 卡片 + 世界书最小 + 组装 + 聊天 + QQ 适配 | **约 14–20 人日** |
| **MVP（方案 A，混合）** | 上表去掉 PNG 写入、加上 API 对接 | **约 13–18 人日** |
| **完整兼容（含 timed effects/群聊/outlet/递归分级）** | — | **约 28–40 人日** |

**优先级建议**：世界书引擎的"关键词 + constant + selective + position/depth + 预算"是 80% 体验的来源，先把这 4–6 人日做好；timed effects 与 inclusion group 属于长尾，可后置（QQ 场景下 sticky/cooldown 的观感收益低于其实现与调试成本）。

---

## 9. 情报缺口清单（下次接手请优先补）

| # | 缺口 | 为什么没拿到 | 补齐办法 |
|---|---|---|---|
| 1 | `checkWorldInfo()` / `getSortedEntries()` 函数体（激活判定细节、排序、预算丢弃顺序、character filter 判定、decorators 处理） | `public/scripts/world-info.js` 265,081 B，`web_fetch` 只给头+尾，中段被省略 | 本机 clone ST（有网环境）后读 `public/scripts/world-info.js`；或分批抓取（需能按字节范围取文件的代理） |
| 2 | `newWorldInfoEntryDefinition` 的**完整默认值表**（我只逐字拿到 27 个字段的默认值，来自 `convertWorldInfoToCharacterBook`） | 同上（定义在文件中部） | 同上；重点核验 `order` 默认（我标 ⚠️ 100）、`role` 枚举 1/2、`delayUntilRecursionLevel` 字段名 |
| 3 | `src/endpoints/backends/text-completions.js` 的路由名与 payload（是否有 `/generate`） | gitcode 403「暂不支持预览」，gitee 镜像同文件也 403 | GitHub blob 页 + base64 blob API（23 KB，可接受）；或在有网机器上 `grep router.post` |
| 4 | `src/server-main.js` 1.19.0 版与 1.18.0 的差异（我用的是 gitee 1.18.0 镜像） | gitcode 403 | 同上（17.8 KB） |
| 5 | `SPEC_V3.md` 全文（48,247 B）与 `concepts.md`（9,851 B） | `raw.githubusercontent.com` DNS 不可达，base64 渠道 token 成本过高 | api.github.com blob（`cb9749821955047890ac88aa84ec0b235ae0fb5d`）→ 本地 base64 解码；重点核验 `assets` 条目字段名（`type/name/ext/uri` 已从 `src/charx.js` 侧面确认）与 group-only greeting 语义 |
| 6 | V2 `spec_v2.md`（7,446 B）中 `character_book.scan_depth` / `token_budget` / `recursive_scanning` 与 entry 的 `name`/`priority`/`case_sensitive` | 同上（仓库未上 jsDelivr） | api blob（`cde6a3bf77631ff1f298583d4af8d079e4b21cdb`）→ 本地解码 |
| 7 | `chat_metadata` 完整键表（`note_*` / `world_info` / `persona` / `variables` / branch 源） | `public/scripts/authors-note.js` 与 `public/script.js`（>1 MB）未抓取 | 抓 `public/scripts/authors-note.js`（小文件）+ `grep saveMetadata` |
| 8 | 真实聊天文件里 `send_date` 的类型、`extra` 全键表、swipes 结构 | 无样例文件；`tests/chat-info.test.js` 只给了 ISO 字符串样例 | 找一份真实导出（或 ST 官方 Discourse/Discord 示例）；`/api/chats/export format=jsonl` 拉自己的一份 |
| 9 | `groups/<id>.json` 的完整字段（`members`/`disabled_members`/`generation_mode`/`reply_strategy`/`activation_strategy`/`fav`） | `src/endpoints/groups.js`（10,782 B）未抓取 | 直接抓该文件（小，gitcode 大概率可用） |
| 10 | `public/scripts/openai.js` 的历史裁剪循环与 EM 挤出算法 | 文件过大 | 在有网环境 `grep -n "openai_max_context\|slice(\|maxTokens"` |
| 11 | `config.yaml` 默认值与 CLI 开关全表（`basicAuthMode`、`whitelistMode`、`disableCsrf`、cookie 名） | `src/command-line.js`（18,428 B）未抓取 | 抓该文件 + 文档 `administration/` 页 |
| 12 | 世界书 token 预算里 `ignoreBudget` / sticky 免预算的精确优先级 | 在省略的中段 | 见 #1 |
| 13 | TC 路线 `story_string` 默认模板 | `power-user.js` 过大 | 抓 `default/content/presets/context/*.json`（应含默认 story string）⚠️ 疑似路径 |

---

## 10. 参考链接（全部可点击）

**SillyTavern 源码（release，1.19.0；gitcode 镜像 `https://raw.gitcode.com/GitHub_Trending/si/SillyTavern/raw/release/<path>` 同样可读）**
- 仓库树：<https://github.com/SillyTavern/SillyTavern/tree/release>
- `src/server-main.js`（API/CSRF/basicAuth/静态资源）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/server-main.js>
- `src/server-startup.js`（router 挂载 + 老路径 308 重定向）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/server-startup.js>
- `src/endpoints/characters.js`（卡片 CRUD/导入导出/`convertWorldInfoToCharacterBook`）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js>
- `src/endpoints/chats.js`（jsonl 读写/备份/integrity）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/chats.js>
- `src/endpoints/worldinfo.js`：<https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/worldinfo.js>
- `src/endpoints/backends/chat-completions.js`（`/generate` `/status` `/bias`）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/backends/chat-completions.js>
- `src/character-card-parser.js`（PNG tEXt chara/ccv3）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/character-card-parser.js>
- `src/charx.js`（V3 CharX/ZIP/assets）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/charx.js>
- `src/validator/TavernCardValidator.js`（V1/V2/V3 必填字段）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/validator/TavernCardValidator.js>
- `public/scripts/world-info.js`（世界书引擎；`#L855` 位置枚举、`#L892` `getWorldInfoPrompt`、`#L462` `getScore`）：<https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/world-info.js>
- `default/content/presets/openai/Default.json`（默认 prompt_order）：<https://github.com/SillyTavern/SillyTavern/blob/release/default/content/presets/openai/Default.json>
- `tests/chat-info.test.js`（jsonl 样例）：<https://github.com/SillyTavern/SillyTavern/blob/release/tests/chat-info.test.js>

**规范**
- Character Card V2 规范仓库：<https://github.com/malfoyslastname/character-card-spec-v2>（`spec_v1.md`、`spec_v2.md`、`keyword_definitions.md`）
- Character Card V3 规范仓库：<https://github.com/kwaroran/character-card-spec-v3>（`SPEC_V3.md`、`concepts.md`）
- V3 字段级第三方实现（Rust）：<https://docs.rs/chara_card/latest/chara_card/raw/v3/struct.CharacterCardData.html>

**官方文档**
- World Info：<https://docs.sillytavern.app/usage/core-concepts/worldinfo/>（`.md`：<https://docs.sillytavern.app/usage/core-concepts/worldinfo.md>）
- Prompt Manager：<https://docs.sillytavern.app/usage/prompts/prompt-manager/>（`.md` 版可读）
- Prompt building（Main Prompt / PHI / World Info 定位）：<https://docs.sillytavern.app/usage/prompts/>（`.md`：`/usage/prompts/index.md`）
- Author's Note：<https://docs.sillytavern.app/usage/characters/authors-note/>（`.md`）
- Character Design（永久 token / first message / EM / 高级字段）：<https://docs.sillytavern.app/usage/characters/characterdesign/>（`.md`）
- Chat File Management（导出/checkpoint/rename）：<https://docs.sillytavern.app/usage/characters/chatfilemanagement/>（`.md`）
- World Info Encyclopedia（社区百科，文档里推荐）：<https://rentry.co/world-info-encyclopedia>（本机不可达，仅留档）

**未采信但相关**：`deepwiki.com/SillyTavern/SillyTavern/10.3-backend-api-development`（持续 429，可作为后续交叉验证源）。
