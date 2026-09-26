# 用 VS Code 把项目传到 GitHub

> 全程在 VS Code 里点，**不用敲命令行**。
> 仓库大小约 **183 MB / 1286 个文件**。

---

## ⚠️ 第 0 步 · 打开文件夹时别点错（这一步最关键）

在 VS Code 里：**File → Open Folder**，选：

```
D:\PROJECT\scenic_ai\重构数字人
```

**必须正好是这个文件夹**，不能是外面的 `D:\PROJECT\scenic_ai`。
选错了，git 的根目录就错了，整个仓库结构会跟着错。

打开后左侧资源管理器里，第一层应该直接看到 `README.md`、`AI-guide-main/`、`Fay-main/`、`plugins/`、`docs/`。

---

## 第 1 步 · 初始化仓库

1. 按 **`Ctrl + Shift + G`** 打开左侧的 **Source Control（源代码管理）**面板
2. 会看到一个蓝色的 **「Initialize Repository（初始化仓库）」** 按钮 → 点它

> 如果没看到这个按钮，而是直接列出了改动，说明这个文件夹里已经有 `.git` 了 —— 那就不用初始化，跳到第 3 步。

---

## 第 2 步 · 检查文件数（确认 `.gitignore` 生效）

初始化之后，面板里会出现 **Changes** 分组。

**点一下分组标题把它展开**，看旁边那个数字：

- ✅ 应该是 **1200 多**
- ❌ 如果是 **几万**，说明 `.gitignore` 没起作用，**先别提交**，告诉我

这一步是为了确认 `node_modules`（4GB）、`.venv`（1.2GB）、`.next`（3GB）没被算进去。

---

## 第 3 步 · 提交

1. 在面板顶部的输入框里输入提交信息：

   ```
   初始提交：景区陪伴数字人（Fay + Live2D 前端 + 知识库插件）
   ```

2. 点输入框右边那个 **✓（Commit）** 按钮
3. 会弹一个提示问 "would you like to stage all your changes and commit them directly?" → 选 **Yes**

提交完 Changes 列表会变空。

---

## 第 4 步 · 关联已经建好的 GitHub 仓库

你之前已经在 GitHub 上建过 `kaerwzno/my-scenic-ai`（还是空的），所以这里直接关联它，不用再建。

1. 按 **`Ctrl + Shift + P`** 打开命令面板
2. 输入 **`Git: Add Remote`** → 回车
3. 弹出的第一个框填 **`origin`** → 回车
4. 第二个框填这个地址 → 回车：

   ```
   https://github.com/kaerwzno/my-scenic-ai.git
   ```

---

## 第 5 步 · 推送

两种方式，任选：

**方式一**：命令面板（`Ctrl+Shift+P`）→ 输入 **`Git: Push`** → 回车

**方式二**：Source Control 面板左上角有个 **`...`** → **Pull, Push → Push**

### 推送时会发生什么

- 第一次会弹浏览器让你登录 GitHub → 点授权
- 然后开始传，**183MB 大概要 1–3 分钟**，界面下方会显示进度
- 成功的标志：左下角状态栏的分支名旁边不再是 `↑1`，刷新 GitHub 页面能看到文件

---

## 如果又报 `HTTP 408` / `unexpected disconnect`

说明是网络把长连接掐了（跟仓库内容无关）。**最可靠的办法是分两次推**，让每次连接都短一些。

在 VS Code 里这么做：

1. 先推**不含 Live2D 模型**的部分 —— 在 Source Control 面板里，找到 `AI-guide-main/public/sentio/characters/free/` 这个文件夹，**右键 → Add to .gitignore**
2. 提交一次（信息写「代码部分」）→ 推送 → **这次只有约 100MB，成功率高很多**
3. 推完之后，再打开 `.gitignore`，把刚加的那行删掉
4. 提交一次（信息写「补充 12 个 Live2D 形象素材」）→ 再推送

第二趟会传约 86MB，一般也能过。

---

## 推完之后建议确认三件事

1. 刷新 https://github.com/kaerwzno/my-scenic-ai ，首页应该显示 `README.md` 的内容
2. 仓库文件列表里**不该有**：`node_modules/`、`.next/`、`.venv/`
3. 搜索一下仓库里有没有 `system.conf` —— **不该有**（那个文件里有你的百炼 API Key，已经被 `.gitignore` 挡住）

---

## 以后怎么更新

改完代码后，在 Source Control 面板里：

1. 写提交信息
2. 点 ✓ 提交
3. 点 **Sync Changes**（或命令面板 `Git: Push`）

就这三步。
