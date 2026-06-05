# 卡皮巴菲特笔记大全

卡皮巴菲特的投资笔记站，专注美股长期投资、价值投资、资产配置和财务自由相关内容。

站点基于 [AstroPaper](https://github.com/satnaing/astro-paper) 改造，使用 Astro 生成静态页面，适合部署到 Cloudflare Pages、Vercel、Netlify 或任意静态托管服务。

## 项目信息

- 仓库：https://github.com/Fe1ix-deng/kapi-buffet-blog
- 站点：https://blog.020023.xyz
- 语言：简体中文
- 时区：Asia/Shanghai
- 内容格式：Markdown / MDX

## 技术栈

- [Astro](https://astro.build/)
- [TypeScript](https://www.typescriptlang.org/)
- [Tailwind CSS](https://tailwindcss.com/)
- [Pagefind](https://pagefind.app/) 静态搜索
- [Satori](https://github.com/vercel/satori) + [Sharp](https://sharp.pixelplumbing.com/) 动态 OG 图片

## 目录结构

```bash
.
├── public/                    # 静态资源
├── src/
│   ├── components/            # 页面组件
│   ├── content/
│   │   ├── pages/             # 关于页等固定页面
│   │   └── posts/             # 文章 Markdown / MDX
│   ├── i18n/                  # 界面文案
│   ├── layouts/               # 页面布局
│   ├── pages/                 # Astro 路由
│   ├── styles/                # 全局样式与主题
│   └── utils/                 # 工具函数
├── astro-paper.config.ts      # 站点与主题配置
├── astro.config.ts            # Astro 配置
└── package.json
```

## 写作

所有文章放在 `src/content/posts/` 下。每篇文章需要包含 frontmatter：

```yaml
---
title: "文章标题"
author: "Kapi buffet"
pubDatetime: 2026-01-01
description: "文章摘要"
featured: false
draft: false
tags:
  - 美股
  - 长期投资
---
```

常用字段：

- `title`：文章标题
- `pubDatetime`：发布时间
- `modDatetime`：更新时间，可选
- `description`：摘要，用于列表和 SEO
- `featured`：是否展示在首页精选区
- `draft`：是否为草稿
- `tags`：标签列表
- `ogImage`：自定义分享图，可选

## 本地开发

项目需要 Node.js 22.12 或更高版本。

```bash
pnpm install
pnpm dev
```

默认开发地址：

```text
http://localhost:4321/
```

## 常用命令

| 命令                | 说明                                       |
| ------------------- | ------------------------------------------ |
| `pnpm dev`          | 启动本地开发服务                           |
| `pnpm build`        | 类型检查、构建站点并生成 Pagefind 搜索索引 |
| `pnpm preview`      | 本地预览构建产物                           |
| `pnpm lint`         | 运行 ESLint                                |
| `pnpm format`       | 格式化代码                                 |
| `pnpm format:check` | 检查格式                                   |

## 配置入口

主要站点配置在 `astro-paper.config.ts`：

- `site`：站点地址、标题、描述、作者、语言、时区
- `posts`：分页数量和定时文章容忍时间
- `features`：搜索、归档、动态 OG 图、暗色模式等开关
- `socials`：首页社交链接
- `shareLinks`：文章页分享链接

## 搜索说明

搜索功能使用 Pagefind。开发模式下首次打开搜索页可能没有结果，需要先运行：

```bash
pnpm build
```

构建完成后，搜索索引会生成到 `dist/pagefind`，并复制到 `public/pagefind` 供本地开发使用。

## 部署

构建命令：

```bash
pnpm build
```

构建产物目录：

```text
dist/
```

部署到静态托管平台时，将构建命令设置为 `pnpm build`，发布目录设置为 `dist`。

## 许可

本项目基于 AstroPaper 改造。原主题遵循 MIT License。
