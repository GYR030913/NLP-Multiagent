// Meeting room booking UI logic (vanilla JS, no dependencies).
"use strict";

const api = {
  rooms: () => fetch("/api/rooms").then(r => r.json()),
  availability: (date, start, end) =>
    fetch(`/api/availability?date=${encodeURIComponent(date)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`).then(r => r.json()),
  bookings: () => fetch("/api/bookings").then(r => r.json()),
  dashboard: (date) =>
    fetch(`/api/dashboard?date=${encodeURIComponent(date)}`).then(r => r.json()),
  book: (payload) =>
    fetch("/api/bookings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  cancel: (id) => fetch(`/api/bookings/${id}/cancel`, { method: "POST" }),
};

function el(id) { return document.getElementById(id); }

function showMessage(text, isError) {
  const box = el("message");
  box.textContent = text;
  box.className = isError ? "msg error" : "msg ok";
}

function defaultDate() {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${mm}-${dd}`;
}

async function loadRooms() {
  const data = await api.rooms();
  const tbody = el("rooms-body");
  tbody.innerHTML = "";
  data.rooms.forEach(room => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${room.id}</td><td>${room.name}</td><td>${room.capacity}</td>` +
      `<td>${room.location}</td><td>${room.equipment}</td>`;
    tbody.appendChild(tr);
  });
  // fill room selector
  const select = el("book-room");
  select.innerHTML = "";
  data.rooms.forEach(room => {
    const opt = document.createElement("option");
    opt.value = room.id;
    opt.textContent = `${room.name} (cap ${room.capacity})`;
    select.appendChild(opt);
  });
}

async function checkAvailability() {
  const date = el("avail-date").value;
  const start = el("avail-start").value;
  const end = el("avail-end").value;
  const res = await fetch(`/api/availability?date=${encodeURIComponent(date)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
  const data = await res.json();
  if (!res.ok) { showMessage(data.error || "Error", true); return; }
  const tbody = el("avail-body");
  tbody.innerHTML = "";
  data.rooms.forEach(entry => {
    const tr = document.createElement("tr");
    const badge = entry.status === "free" ? "<span class='free'>free</span>" : "<span class='busy'>busy</span>";
    tr.innerHTML = `<td>${entry.room.name}</td><td>${badge}</td><td>${entry.conflicts.length}</td>`;
    tbody.appendChild(tr);
  });
  el("avail-summary").textContent =
    `${data.rooms_free} free / ${data.rooms_busy} busy for ${data.start} to ${data.end}`;
  showMessage("Availability updated", false);
}

async function createBooking() {
  const payload = {
    room_id: parseInt(el("book-room").value, 10),
    date: el("book-date").value,
    start: el("book-start").value,
    end: el("book-end").value,
    booker: el("book-booker").value,
    title: el("book-title").value,
  };
  const res = await api.book(payload);
  const data = await res.json();
  if (!res.ok) { showMessage(data.error || "Booking rejected", true); return; }
  showMessage(`Booked #${data.booking.id} for ${data.booking.start_dt} to ${data.booking.end_dt}`, false);
  await loadBookings();
}

async function loadBookings() {
  const [data, roomData] = await Promise.all([api.bookings(), api.rooms()]);
  const roomNames = new Map(roomData.rooms.map(room => [String(room.id), room.name]));
  const tbody = el("bookings-body");
  tbody.innerHTML = "";
  data.bookings.forEach(b => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${b.id}</td><td></td><td>${b.start_dt}</td>` +
      `<td>${b.end_dt}</td><td>${b.booker}</td><td>${b.status}</td>` +
      `<td>${b.status === "active" ? `<button data-id="${b.id}" class="cancel">Cancel</button>` : "-"}</td>`;
    tr.children[1].textContent = roomNames.get(String(b.room_id)) || `Unknown room (#${b.room_id})`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("button.cancel").forEach(btn => {
    btn.addEventListener("click", () => cancelBooking(btn.dataset.id));
  });
}

async function cancelBooking(id) {
  const res = await api.cancel(id);
  const data = await res.json();
  if (!res.ok) { showMessage(data.error || "Cancel failed", true); return; }
  showMessage(`Cancelled booking #${id}; slot is free again`, false);
  await loadBookings();
  await refreshDashboard();
}

async function refreshDashboard() {
  const date = el("dash-date").value;
  const data = await api.dashboard(date || null);
  el("dash-rooms").textContent = data.total_rooms;
  el("dash-active").textContent = data.active_bookings;
  el("dash-cancelled").textContent = data.cancelled_bookings;
}

function init() {
  const today = defaultDate();
  el("avail-date").value = today;
  el("book-date").value = today;
  el("dash-date").value = today;
  el("btn-availability").addEventListener("click", checkAvailability);
  el("btn-book").addEventListener("click", createBooking);
  el("btn-refresh-bookings").addEventListener("click", loadBookings);
  el("btn-refresh-dash").addEventListener("click", refreshDashboard);
  loadRooms().then(loadBookings).then(refreshDashboard).catch(err => showMessage(String(err), true));
}

document.addEventListener("DOMContentLoaded", init);
