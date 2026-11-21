"""Stable date-range dashboard contract; all timestamps normalized to Hong Kong."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from collections import Counter
TZ=ZoneInfo("Asia/Hong_Kong")
def local(value):
    dt=datetime.fromisoformat(value) if isinstance(value,str) else value
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)
def compute_analytics(rows,start_date,end_date,*,rooms=()):
    start_day=date.fromisoformat(str(start_date)); end_day=date.fromisoformat(str(end_date))
    if end_day<start_day or (end_day-start_day).days>3660:
        raise ValueError("Select an ordered date range of at most 3661 days")
    start=datetime.combine(start_day,time.min,TZ);end=datetime.combine(end_day+timedelta(days=1),time.min,TZ)
    by_room={r["id"]:{"room_id":r["id"],"name":r["name"],"count":0,"hours":0.0} for r in rooms}
    days=Counter();hours=Counter();total=cancelled=0
    for row in rows:
        rid=row["room_id"]
        by_room.setdefault(rid,{"room_id":rid,"name":str(rid),"count":0,"hours":0.0})
        lo=max(local(row["start_dt"]),start);hi=min(local(row["end_dt"]),end)
        if hi<=lo:continue
        status=row["status"]
        if status not in {"active","cancelled"}:raise ValueError("Unknown booking status")
        total+=1
        if status=="cancelled":cancelled+=1;continue
        info=by_room[rid];info["count"]+=1;info["hours"]+=(hi-lo).total_seconds()/3600
        d=lo.date()
        while datetime.combine(d,time.min,TZ)<hi:
            days[d.isoformat()]+=1;d+=timedelta(days=1)
        cursor=lo.replace(minute=0,second=0,microsecond=0)
        while cursor<hi:
            hours[cursor.hour]+=1;cursor+=timedelta(hours=1)
    ordered=sorted(by_room.values(),key=lambda r:(-r["count"],str(r["room_id"])))
    for row in ordered:row["hours"]=round(row["hours"],6)
    peak_day=min(days,key=lambda d:(-days[d],d)) if days else None
    peak_hour=min(hours,key=lambda h:(-hours[h],h)) if hours else None
    return {"window":{"start":start_date,"end":end_date,"timezone":"Asia/Hong_Kong","inclusive":True},
            "rooms":ordered,"peak_day":peak_day,"peak_hour":peak_hour,
            "peak_day_count":days.get(peak_day,0),"peak_hour_count":hours.get(peak_hour,0),
            "cancellations":{"count":cancelled,"denominator":total,"rate":cancelled/total if total else 0},
            "total_reserved_hours":round(sum(r["hours"] for r in ordered),6),
            "data_kind":"booking records from configured database; not synthetic fixture"}
aggregate=compute_analytics
