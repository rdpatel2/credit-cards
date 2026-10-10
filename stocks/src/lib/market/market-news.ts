import type { NewsItem } from "./types";
import { classifySectors, type Sector } from "./research";
import { fetchPublic, parseNewsFeed } from "./providers";
import { DEMO_AS_OF } from "./demo";
export type MarketArticle = NewsItem & { sectors: Exclude<Sector, "all">[] };
export type MarketNews = {
  articles: MarketArticle[];
  unavailableFeeds: string[];
};
const feeds = [
  { name: "Yahoo Finance", url: "https://finance.yahoo.com/news/rssindex" },
  {
    name: "CNBC Markets",
    url: "https://www.cnbc.com/id/100003114/device/rss/rss.html",
  },
];
export function combineArticles(items: NewsItem[]): MarketArticle[] {
  const seenUrls = new Set<string>();
  const seenTitles = new Set<string>();
  return items
    .sort((a, b) => Date.parse(b.publishedAt) - Date.parse(a.publishedAt))
    .flatMap((item) => {
      const url = new URL(item.url);
      // Ignore common campaign parameters when identifying syndicated duplicates.
      for (const key of [...url.searchParams.keys()])
        if (/^(utm_|guccounter|guce_referrer)/.test(key))
          url.searchParams.delete(key);
      url.hash = "";
      const title = item.title.toLowerCase().replace(/[^a-z0-9]/g, "");
      if (seenUrls.has(url.href) || seenTitles.has(title)) return [];
      seenUrls.add(url.href);
      seenTitles.add(title);
      return [{ ...item, url: url.href, sectors: classifySectors(item.title) }];
    })
    .slice(0, 120);
}
export async function marketNews(): Promise<MarketNews> {
  const responses = await Promise.allSettled(
    feeds.map(async (feed) =>
      parseNewsFeed(await (await fetchPublic(feed.url)).text(), feed.name, 80),
    ),
  );
  const articles: NewsItem[] = [];
  const unavailableFeeds: string[] = [];
  responses.forEach((response, i) => {
    if (response.status === "fulfilled" && response.value.length)
      articles.push(...response.value);
    else unavailableFeeds.push(feeds[i].name);
  });
  if (!articles.length) throw new Error("Market news unavailable");
  return { articles: combineArticles(articles), unavailableFeeds };
}
export function demoMarketNews(): MarketNews {
  const titles = [
    "Nvidia and Microsoft put AI infrastructure investment in focus",
    "Banks assess loan demand as the interest-rate outlook shifts",
    "Biotech investors track FDA decisions and drug pipelines",
    "Oil producers weigh OPEC supply outlook and refinery demand",
    "Tesla and Amazon highlight changing consumer spending",
    "Grocery retailers and Costco focus on household budgets",
    "Airlines and aerospace manufacturers outline investment plans",
    "Copper miners assess demand from new industrial projects",
    "Real estate investors monitor commercial property refinancing",
    "Utilities plan power grid investment to meet electricity demand",
    "Netflix and Meta compete for streaming and advertising spending",
    "Wall Street prepares for inflation data and Federal Reserve commentary",
    "Technology and bank stocks lead a broad market discussion",
    "Healthcare companies discuss margins and research spending",
  ];
  return {
    articles: combineArticles(
      titles.map((title, i) => ({
        title,
        url: `https://finance.yahoo.com/news/?demo-story=${i}`,
        publisher: "Illustrative headline",
        publishedAt: new Date(
          Date.parse(DEMO_AS_OF) - i * 3600000,
        ).toISOString(),
      })),
    ),
    unavailableFeeds: [],
  };
}
