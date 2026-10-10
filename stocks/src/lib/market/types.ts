import type { Range } from "@/lib/workspace";
export type Source = {
  provider: string;
  demo: boolean;
  asOf: string;
  message?: string;
};
export type Quote = {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  currency: string;
  volume: number | null;
  high: number | null;
  low: number | null;
  yearHigh: number | null;
  yearLow: number | null;
  previousClose: number;
  points: { time: number; close: number }[];
  asOf: string;
};
export type NewsItem = {
  title: string;
  url: string;
  publisher: string;
  publishedAt: string;
};
export type EarningsItem = {
  symbol: string;
  name: string;
  date: string;
  time: string;
  estimate: string;
};
export type DataEnvelope<T> = { data: T; source: Source };
export interface MarketProvider {
  id: string;
  quote(symbol: string, range: Range): Promise<Quote>;
}
