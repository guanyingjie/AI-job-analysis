"""School alias registry – maps user input to canonical school metadata."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SchoolEntry:
    full_name: str
    short_names: list[str] = field(default_factory=list)
    prefecture: str = ""
    wiki_title: str = ""
    kyureki_id: str = ""


_SCHOOLS: list[SchoolEntry] = [
    SchoolEntry(
        full_name="大阪桐蔭高等学校",
        short_names=["大阪桐蔭", "桐蔭"],
        prefecture="大阪府",
        wiki_title="大阪桐蔭中学校・高等学校",
        kyureki_id="27063",
    ),
    SchoolEntry(
        full_name="智辯学園和歌山高等学校",
        short_names=["智弁和歌山", "智辯和歌山", "智弁学園和歌山"],
        prefecture="和歌山県",
        wiki_title="智辯学園和歌山小学校・中学校・高等学校",
        kyureki_id="30003",
    ),
    SchoolEntry(
        full_name="横浜高等学校",
        short_names=["横浜", "横浜高校"],
        prefecture="神奈川県",
        wiki_title="横浜中学校・高等学校",
        kyureki_id="14033",
    ),
    SchoolEntry(
        full_name="PL学園高等学校",
        short_names=["PL学園", "PL"],
        prefecture="大阪府",
        wiki_title="PL学園中学校・高等学校",
        kyureki_id="27049",
    ),
    SchoolEntry(
        full_name="中京大学附属中京高等学校",
        short_names=["中京大中京", "中京"],
        prefecture="愛知県",
        wiki_title="中京大学附属中京高等学校",
        kyureki_id="23059",
    ),
    SchoolEntry(
        full_name="東海大学付属相模高等学校",
        short_names=["東海大相模", "東海大付属相模"],
        prefecture="神奈川県",
        wiki_title="東海大学付属相模高等学校・中等部",
        kyureki_id="14035",
    ),
    SchoolEntry(
        full_name="仙台育英学園高等学校",
        short_names=["仙台育英"],
        prefecture="宮城県",
        wiki_title="仙台育英学園高等学校",
        kyureki_id="04005",
    ),
    SchoolEntry(
        full_name="花巻東高等学校",
        short_names=["花巻東"],
        prefecture="岩手県",
        wiki_title="花巻東高等学校",
        kyureki_id="03013",
    ),
    SchoolEntry(
        full_name="金足農業高等学校",
        short_names=["金足農業", "金足農", "カナノウ"],
        prefecture="秋田県",
        wiki_title="秋田県立金足農業高等学校",
        kyureki_id="05003",
    ),
    SchoolEntry(
        full_name="明徳義塾高等学校",
        short_names=["明徳義塾", "明徳"],
        prefecture="高知県",
        wiki_title="明徳義塾中学校・高等学校",
        kyureki_id="39002",
    ),
    SchoolEntry(
        full_name="駒澤大学附属苫小牧高等学校",
        short_names=["駒大苫小牧", "駒苫"],
        prefecture="北海道",
        wiki_title="駒澤大学附属苫小牧高等学校",
        kyureki_id="01010",
    ),
    SchoolEntry(
        full_name="報徳学園高等学校",
        short_names=["報徳学園", "報徳"],
        prefecture="兵庫県",
        wiki_title="報徳学園中学校・高等学校",
        kyureki_id="28052",
    ),
    SchoolEntry(
        full_name="龍谷大学付属平安高等学校",
        short_names=["龍谷大平安", "京都平安", "平安"],
        prefecture="京都府",
        wiki_title="龍谷大学付属平安高等学校・中学校",
        kyureki_id="26018",
    ),
    SchoolEntry(
        full_name="早稲田実業学校高等部",
        short_names=["早実", "早稲田実業"],
        prefecture="東京都",
        wiki_title="早稲田大学系属早稲田実業学校初等部・中等部・高等部",
        kyureki_id="13048",
    ),
    SchoolEntry(
        full_name="天理高等学校",
        short_names=["天理"],
        prefecture="奈良県",
        wiki_title="天理高等学校",
        kyureki_id="29001",
    ),
    SchoolEntry(
        full_name="帝京高等学校",
        short_names=["帝京"],
        prefecture="東京都",
        wiki_title="帝京中学校・高等学校",
        kyureki_id="13022",
    ),
    SchoolEntry(
        full_name="日本大学第三高等学校",
        short_names=["日大三", "日大三高"],
        prefecture="東京都",
        wiki_title="日本大学第三中学校・高等学校",
        kyureki_id="13023",
    ),
    SchoolEntry(
        full_name="履正社高等学校",
        short_names=["履正社"],
        prefecture="大阪府",
        wiki_title="履正社学園豊中中学校・履正社高等学校",
        kyureki_id="27062",
    ),
    SchoolEntry(
        full_name="星稜高等学校",
        short_names=["星稜"],
        prefecture="石川県",
        wiki_title="星稜中学校・高等学校",
        kyureki_id="17003",
    ),
    SchoolEntry(
        full_name="広陵高等学校",
        short_names=["広陵"],
        prefecture="広島県",
        wiki_title="広陵高等学校 (広島県)",
        kyureki_id="34010",
    ),
    SchoolEntry(
        full_name="常総学院高等学校",
        short_names=["常総学院"],
        prefecture="茨城県",
        wiki_title="常総学院中学校・高等学校",
        kyureki_id="08004",
    ),
    SchoolEntry(
        full_name="日本文理高等学校",
        short_names=["日本文理"],
        prefecture="新潟県",
        wiki_title="日本文理高等学校",
        kyureki_id="15004",
    ),
    SchoolEntry(
        full_name="光星学院高等学校",
        short_names=["八戸学院光星", "光星"],
        prefecture="青森県",
        wiki_title="八戸学院光星高等学校",
        kyureki_id="02008",
    ),
    SchoolEntry(
        full_name="聖光学院高等学校",
        short_names=["聖光学院"],
        prefecture="福島県",
        wiki_title="聖光学院高等学校 (福島県)",
        kyureki_id="07016",
    ),
    SchoolEntry(
        full_name="作新学院高等学校",
        short_names=["作新学院"],
        prefecture="栃木県",
        wiki_title="作新学院高等学校",
        kyureki_id="09007",
    ),
    SchoolEntry(
        full_name="高松商業高等学校",
        short_names=["高松商", "高松商業"],
        prefecture="香川県",
        wiki_title="香川県立高松商業高等学校",
        kyureki_id="37001",
    ),
    SchoolEntry(
        full_name="松山商業高等学校",
        short_names=["松山商", "松山商業"],
        prefecture="愛媛県",
        wiki_title="愛媛県立松山商業高等学校",
        kyureki_id="38001",
    ),
    SchoolEntry(
        full_name="東邦高等学校",
        short_names=["東邦"],
        prefecture="愛知県",
        wiki_title="東邦高等学校",
        kyureki_id="23025",
    ),
    SchoolEntry(
        full_name="興南高等学校",
        short_names=["興南"],
        prefecture="沖縄県",
        wiki_title="興南中学校・高等学校",
        kyureki_id="47001",
    ),
    SchoolEntry(
        full_name="智辯学園高等学校",
        short_names=["智弁学園", "智辯学園"],
        prefecture="奈良県",
        wiki_title="智辯学園中学校・高等学校",
        kyureki_id="29010",
    ),
]

_INDEX: dict[str, SchoolEntry] = {}
for _entry in _SCHOOLS:
    _INDEX[_entry.full_name] = _entry
    for _alias in _entry.short_names:
        _INDEX[_alias] = _entry


def resolve(user_input: str) -> SchoolEntry | None:
    """Return the matching *SchoolEntry* or ``None`` if no match."""
    cleaned = user_input.strip()
    if cleaned in _INDEX:
        return _INDEX[cleaned]
    for key, entry in _INDEX.items():
        if key in cleaned or cleaned in key:
            return entry
    return None
