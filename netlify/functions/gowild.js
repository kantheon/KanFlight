// GoWild availability checker — hits Frontier booking endpoint
export default async (req) => {
  if (req.method !== "POST") return new Response("POST only", { status: 405 });

  const { origins, destination, date } = await req.json();
  if (!origins?.length || !destination || !date) {
    return Response.json([]);
  }

  const allFlights = [];

  for (const origin of origins) {
    try {
      const flights = await checkGoWild(origin, destination, date);
      allFlights.push(...flights);
    } catch (e) {
      console.error(`GoWild ${origin}→${destination}: ${e.message}`);
    }
  }

  allFlights.sort((a, b) => (a.price || 999) - (b.price || 999));
  return Response.json(allFlights);
};

async function checkGoWild(origin, dest, date) {
  const dt = new Date(date + "T00:00:00");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const dateFmt = `${months[dt.getMonth()]}-${String(dt.getDate()).padStart(2,"0")}, ${dt.getFullYear()}`;

  const url = `https://booking.flyfrontier.com/Flight/InternalSelect?o1=${origin}&d1=${dest}&dd1=${encodeURIComponent(dateFmt)}&ADT=1&mon=true&promo=`;

  const resp = await fetch(url, {
    headers: {
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    },
  });

  if (!resp.ok) return [];
  let text = await resp.text();

  // Unescape HTML entities
  text = text.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#x27;/g, "'");

  // Find journeys JSON
  let start = text.indexOf('{"journeys"');
  if (start === -1) {
    const idx = text.indexOf('"journeys"');
    if (idx !== -1) start = text.lastIndexOf("{", idx);
  }
  if (start === -1) return [];

  // Find matching brace
  let depth = 0, end = start;
  for (let i = start; i < Math.min(start + 1000000, text.length); i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}") depth--;
    if (depth === 0) { end = i + 1; break; }
  }

  let data;
  try { data = JSON.parse(text.substring(start, end)); } catch { return []; }

  const flights = [];
  const seen = new Set();

  for (const journey of data.journeys || []) {
    for (const flight of journey.flights || []) {
      if (!flight.isGoWildFareEnabled) continue;

      const fnum = flight.flightNumber || (flight.segments?.[0]?.flightNumber || "");
      const depTime = flight.departureTimeFormatted || "";
      const arrTime = flight.arrivalTimeFormatted || "";
      const duration = flight.durationFormatted || "";
      const price = flight.discountDenFare || 0;
      const stops = flight.stops || 0;

      const key = `F9${fnum}_${depTime}`;
      if (seen.has(key)) continue;
      seen.add(key);

      flights.push({
        origin,
        destination: dest,
        price,
        dep_time: depTime,
        arr_time: arrTime,
        duration,
        flight_number: `F9 ${fnum}`,
        stops,
        gowild: true,
      });
    }
  }

  return flights;
}

export const config = { path: "/api/gowild" };
