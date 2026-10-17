"""Shared syntactic rejection of placeholders; this does not validate a real fact."""
import re
import unicodedata

UNSUPPORTED_LABELS=frozenset({'','unknown','unresolved','unspecified','unconfirmed','unknown event',
    'unknown visual event','unconfirmed event','unconfirmed visual event','visual anomaly',
    'violation','no violation','positive','negative','normal','anomaly','true','false','0','1',
    'неизвестно','неизвестный','неподтверждено','нарушение','без нарушения','тип не установлен',
    'тип не определен','тип не подтвержден','визуальный сигнал тип не подтвержден'})


def is_specific_type(value):
    if not isinstance(value,str):return False
    normalized=unicodedata.normalize('NFKC',value).casefold().replace('ё','е')
    normalized=re.sub(r'[\W_]+',' ',normalized,flags=re.UNICODE).strip()
    return (normalized not in UNSUPPORTED_LABELS
            and not normalized.startswith(('unknown ','unconfirmed ','неизвестный ','неподтвержден'))
            and not any(text in normalized for text in ('тип не установлен','тип не определен','не подтвержден')))
