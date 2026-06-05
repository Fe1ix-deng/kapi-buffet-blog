import { defineAstroPaperConfig } from "./src/types/config";

export default defineAstroPaperConfig({
  site: {
    url: "https://blog.020023.xyz",
    title: "Kapi Fund",
    description: "可爱又迷人的正派角色",
    author: "Kapi buffet",
    profile: "https://satna.ing",
    ogImage: "default-og.jpg",
    lang: "zh-CN",
    timezone: "Asia/Shanghai",
    dir: "ltr",
  },
  posts: {
    perPage: 4,
    perIndex: 4,
    scheduledPostMargin: 15 * 60 * 1000,
  },
  features: {
    lightAndDarkMode: true,
    dynamicOgImage: true,
    showArchives: true,
    showBackButton: true,
    search: "pagefind",
  },
  socials: [
    { name: "github", url: "https://github.com/Fe1ix-deng/kapi-buffet-blog" },
    {
      name: "xiaohongshu",
      url: "https://www.xiaohongshu.com/user/profile/6143b19a000000001f0366ca?xsec_token=AB82CW6O1jSZhpkud7D873cxGlY5aV3FVbu0iA55xfrmU=&xsec_source=pc_note",
      linkTitle: "Kapi Fund 的小红书主页",
    },
  ],
  shareLinks: [
    { name: "whatsapp", url: "https://wa.me/?text=" },
    { name: "facebook", url: "https://www.facebook.com/sharer.php?u=" },
    { name: "x",        url: "https://x.com/intent/post?url=" },
    { name: "telegram", url: "https://t.me/share/url?url=" },
    { name: "pinterest", url: "https://pinterest.com/pin/create/button/?url=" },
    { name: "mail",     url: "mailto:?subject=See%20this%20post&body=" },
  ],
});
