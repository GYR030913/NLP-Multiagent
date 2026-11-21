"use strict";
document.addEventListener("DOMContentLoaded", () => {
 const get = id => document.getElementById(id);
 const now = new Intl.DateTimeFormat("en-CA", {timeZone:"Asia/Hong_Kong",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());
 const part = type => now.find(p => p.type === type).value;
 const today = `${part("year")}-${part("month")}-${part("day")}`;
 get("range-start").value=today; get("range-end").value=today;
 let generation=0;
 async function update() {
  const current=++generation;
  get("range-status").textContent="Loading";
  try {
   const response=await fetch(`/api/dashboard?${new URLSearchParams({start:get("range-start").value,end:get("range-end").value})}`);
   const data=await response.json(); if (!response.ok) throw new Error(data.error || "Invalid date range");
   if(current!==generation)return;
   const count=data.rooms.reduce((n,r)=>n+r.count,0);
   get("range-status").textContent=count ? "Usage loaded" : "No active bookings in this date range";
   get("range-summary").textContent=`Active bookings: ${count}; reserved hours: ${data.total_reserved_hours}; peak day: ${data.peak_day || "none"}; peak hour: ${data.peak_hour === null ? "none" : data.peak_hour}; cancellations: ${data.cancellations.count}/${data.cancellations.denominator} (${(100*data.cancellations.rate).toFixed(1)}%)`;
   get("usage-chart").replaceChildren();get("usage-table").replaceChildren();
   const max=Math.max(1,...data.rooms.map(r=>r.count));
   data.rooms.forEach(r=>{
    const bar=document.createElement("div");bar.textContent=`${r.name}: ${r.count}`;
    bar.style.background="#dce8f5";bar.style.margin="4px 0";bar.style.padding="4px 0";bar.style.whiteSpace="nowrap";bar.style.width=`${100*r.count/max}%`;
    get("usage-chart").appendChild(bar);
    const row=document.createElement("tr");[r.name,r.count,r.hours].forEach(v=>{const cell=document.createElement("td");cell.textContent=v;row.appendChild(cell);});get("usage-table").appendChild(row);
   });
   get("range-advice").textContent=count ? `Review allocation: ${data.rooms[0].name} has the most active bookings; compare against ${data.rooms[data.rooms.length-1].name} before reallocating.` : "No allocation recommendation without active usage records.";
   get("range-provenance").textContent=data.data_kind;
  } catch(error) {if(current===generation){get("range-status").textContent=String(error);get("usage-chart").replaceChildren();get("usage-table").replaceChildren();get("range-summary").textContent="";get("range-advice").textContent="";}}
 }
 ["range-start","range-end"].forEach(id=>get(id).addEventListener("change",update));get("range-refresh").addEventListener("click",update);update();
});
