from __future__ import annotations
from typing import Dict, Type
from .base import BaseCrawler
from .lovable import LovableCrawler, LOVABLE_PROJECTS
from .openaffiliate import OpenAffiliateCrawler, OPENAFFILIATE_NETWORKS
from .goaffpro import GoAffProCrawler, GOAFFPRO_MAX_CHOICES
from .apdb_crawler import APDBCrawler, APDB_MAX_CHOICES
from .growthhero import GrowthHeroCrawler, GROWTHHERO_MAX_CHOICES
from .aiaffiliateindex import AIAffiliateIndexCrawler
from .affiliatewatch import AffiliateWatchCrawler, AFFILIATEWATCH_MAX_CHOICES
from .reditus import ReditusCrawler


SOURCES: Dict[str, Type[BaseCrawler]] = {
    "lovable": LovableCrawler,
    "openaffiliate": OpenAffiliateCrawler,
    "goaffpro": GoAffProCrawler,
    "apdb": APDBCrawler,
    "growthhero": GrowthHeroCrawler,
    "aiaffiliateindex": AIAffiliateIndexCrawler,
    "affiliatewatch": AffiliateWatchCrawler,
    "reditus": ReditusCrawler,
}


SOURCE_META = [
    {
        "code": "openaffiliate",
        "name": "OpenAffiliate",
        "base_url": "https://openaffiliate.dev",
        "description": "Directory open-source 750+ affiliate program (PartnerStack, Rewardful, FirstPromoter...). Crawl trực tiếp YAML từ repo GitHub — nhanh, ổn định.",
        "icon_hint": "trophy",
        "highlight": True,
        "options": [
            {"key": "network", "label": "Chọn nền tảng", "choices": OPENAFFILIATE_NETWORKS, "default": "_all"},
        ],
    },
    {
        "code": "lovable",
        "name": "Lovable Directory",
        "base_url": "https://affiliateprogram.lovable.app",
        "description": "Directory tổng hợp affiliate program phân theo nền tảng (Rewardful, FirstPromoter, Tolt…). Crawl thật bằng Nodriver.",
        "icon_hint": "sparkles",
        "highlight": True,
        "options": [
            {"key": "project", "label": "Chọn nền tảng", "choices": LOVABLE_PROJECTS, "default": "rewardful"},
        ],
    },
    {
        "code": "goaffpro",
        "name": "GoAffPro",
        "base_url": "https://goaffpro.com/affiliate/stores/search",
        "description": "Directory ~22.8k shop Shopify dùng GoAffPro (beauty, apparel, supplements, pets…). Pull trực tiếp public API — nhanh, không cần login.",
        "icon_hint": "shopping-bag",
        "highlight": True,
        "options": [
            {"key": "max_stores", "label": "Số lượng shop", "choices": GOAFFPRO_MAX_CHOICES, "default": "2000"},
        ],
    },
    {
        "code": "apdb",
        "name": "Affiliate Program DB",
        "base_url": "https://www.affiliateprogramdb.com",
        "description": "Directory 500+ affiliate program phân theo niche (Forex, Health, Tech…). HTML scraper tĩnh — không cần browser, tự resolve homepage ngay khi crawl.",
        "icon_hint": "database",
        "highlight": True,
        "options": [
            {"key": "max_programs", "label": "Số chương trình", "choices": APDB_MAX_CHOICES, "default": "50"},
        ],
    },
    {
        "code": "growthhero",
        "name": "GrowthHero Marketplace",
        "base_url": "https://www.growthhero.io/affiliate_program_marketplace/",
        "description": "Marketplace ~16k merchant (thời trang, đồ ăn, trang sức, beauty…). Pull public JSON API — nhanh. Có link đăng ký affiliate + commission + category. Trang chủ thật resolve qua 'Tìm trang chủ' (giống Lovable).",
        "icon_hint": "shopping-bag",
        "highlight": True,
        "options": [
            {"key": "max_programs", "label": "Số chương trình", "choices": GROWTHHERO_MAX_CHOICES, "default": "300"},
        ],
    },
    {
        "code": "aiaffiliateindex",
        "name": "AI Affiliate Index",
        "base_url": "https://aiaffiliateindex.com/affiliate-programs",
        "description": "Directory ~148 affiliate program cho AI tool (Simplified, Puppetry…). Có sẵn link affiliate, commission, cookie, category. Trang chủ thật resolve tự động qua 'View AI tool' — không cần browser.",
        "icon_hint": "sparkles",
        "highlight": True,
        "options": [],
    },
    {
        "code": "affiliatewatch",
        "name": "Affiliate.watch",
        "base_url": "https://affiliate.watch",
        "description": "Directory ~902 affiliate program đa ngành. Có sẵn trang chủ, link join affiliate, hoa hồng, cookie, payout, category, network và PHƯƠNG THỨC THANH TOÁN (PayPal/Stripe…). Pull JSON — nhanh, không cần browser.",
        "icon_hint": "trophy",
        "highlight": True,
        "options": [
            {"key": "max_programs", "label": "Số chương trình", "choices": AFFILIATEWATCH_MAX_CHOICES, "default": "902"},
        ],
    },
    {
        "code": "reditus",
        "name": "Reditus Marketplace",
        "base_url": "https://app.getreditus.com/marketplace",
        "description": "Marketplace ~48 chương trình affiliate B2B SaaS (Marketing, Sales, Productivity, Accounting…). Có sẵn hoa hồng + thời hạn, cookie, ngưỡng payout, category, URL sản phẩm. Pull JSON (Next.js RSC) — nhanh, không cần browser.",
        "icon_hint": "trophy",
        "highlight": True,
        "options": [],
    },
]


def get_crawler(source: str, **kwargs) -> BaseCrawler:
    cls = SOURCES.get(source)
    if not cls:
        raise ValueError(f"Source không hỗ trợ: {source}")
    return cls(**kwargs)
