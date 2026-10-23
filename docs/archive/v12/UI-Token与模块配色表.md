# V12 UI Token 与模块配色表

权威来源：冻结的 `public/assets/styles.css` 首行变量及 `.site-hero`、`.hero-copy h1 em`。实现位置：`src/styles/tokens.css`；V12 外壳样式：`src/styles/v12.css`。官网独立静态文件不加载内部样式。

| Token | 值 | 来源 / 用途 |
| --- | --- | --- |
| brand-primary | #335FE1 | 官网 --blue；主要 CTA、激活导航、重点文字 |
| brand-hover | #244DCA | 由官网蓝加深，主按钮悬停 |
| brand-deep | #193D87 | 深蓝运行状态区域 |
| brand-sky | #3D79ED | 官网标题渐变起点，重点线条 |
| brand-tint | #EDF3FF | 官网 --soft-blue；轻蓝信息层 |
| brand-subtle / selected | #F5F8FF / #E3ECFF | 弱背景 / 选中状态 |
| brand-border / focus | #CAD9F8 / #9FB7FF | 蓝色描边 / 键盘焦点；focus 来自官网样式 |
| text-primary | #17213B | 官网 --ink；核心阅读文字 |
| text-heading / secondary | #2B4671 / #536B90 | 标题 / 次要正文 |
| text-muted / caption | #657792 / #7E8CA3 | 正文弱化 / 辅助说明；caption 来自官网 --muted |
| border-subtle | #E6EDF8 | 官网 --line；分隔与卡片边框 |
| surface-canvas / panel | #F5F8FD / #FFFFFF | 淡蓝灰画布 / 白色编辑与表格 |
| gradient-hero | 119deg：#EDF5FF → #F9FCFF → #E7F0FF → #DFEAFE | 官网原渐变，保留为品牌背景 Token |
| gradient-focus | 95deg：#3D79ED → #6578DB | 官网标题强调渐变 |
| gradient-deep | #335FE1 → #193D87 | 自主运营局部运行状态 |
| state-success / warning / danger | #24765E / #91601B / #B23838 | 仅表示成功、警示、错误 |
| shadow-panel / focus / popover | 统一蓝色透明阴影 | 避免黑色厚阴影，浮层保持层级 |

`--blue / --blue-soft / --ink / --muted / --line` 继续作为兼容别名供 G1 组件使用。没有在各业务页重新定义品牌色。圆角分为 16px 主卡、12px 普通信息卡、8px 紧凑控件；旧业务控件逐阶段升级。

| 模块 | R1 已落地 | 后续阶段 |
| --- | --- | --- |
| 官网 | 用户批准仅 Logo 替换；其余 HTML、原 CSS/JS 与三层卡包冻结 | 无页面重构 |
| 登录 | 同系浅蓝视觉、官网原口号、品牌蓝主按钮、新指挥台默认入口 | 正式认证由后端 |
| AI指挥台 | 淡蓝画布、白色大输入框、品牌蓝描边及 CTA、局部深蓝自主运营卡；KPI 移至经营总览入口 | R2 会话态、进度、结构化成果 |
| 经营总览 | 白色三项 KPI 与浅蓝重点区；统一范围不重复新增顶层指标 | R5 数据与复盘细化 |
| 直播/任务/其余旧页 | 使用相同 Shell、蓝色标题线、统一按钮/表格主题；原有能力保留 | R5 直播细节 |
| ADQ/小店/微信豆 | 尚未新建专业界面，只保留旧投放入口 | R3：白色高密度表格、蓝色步骤及右侧概览，各平台独立业务流程 |
| 内容工厂 | 新导航与统一页面主题，原有骨架保留 | R4：白色编辑区、浅蓝成果预览、下载/关联投放优先级 |
| 手机 | 共用正式 Token；新外壳适配 375/390，抽屉导航和主要操作 44px | R5：/mobile 四 Tab 与固定指挥输入细化 |

蓝色集中于导航、输入焦点、CTA 和单个自主状态重点区；主阅读、编辑与表格保持白色。因此同屏具有明确重点，不把全部卡片涂成同一浅蓝。

真实对比截图见 `../evidence/v12/r1/` 的 homepage-after-1440、command-1440、overview-1440、command-390。R3/R4 未实施，未伪造其验收截图。
