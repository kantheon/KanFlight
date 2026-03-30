// Flight search via Google Flights API (same approach as fli library)
export default async (req) => {
  if (req.method !== "POST") return new Response("POST only", { status: 405 });

  const body = await req.json();
  const { origins, destination, date, stops, seat, sort } = body;

  if (!origins?.length || !destination || !date) {
    return Response.json({ error: "Missing origins, destination, or date" }, { status: 400 });
  }

  const allFlights = [];

  for (const origin of origins) {
    try {
      const flights = await searchGoogleFlights(origin, destination, date, stops, seat, sort);
      allFlights.push(...flights);
    } catch (e) {
      console.error(`Search ${origin}→${destination}: ${e.message}`);
    }
  }

  allFlights.sort((a, b) => (a.price || 99999) - (b.price || 99999));
  return Response.json(allFlights);
};

async function searchGoogleFlights(origin, dest, date, stops, seat, sort) {
  // Build the protobuf-like encoded request that Google Flights expects
  const seatMap = { economy: 1, premium: 2, business: 3, first: 4 };
  const sortMap = { cheapest: 2, fastest: 5, best: 1 };
  const stopsMap = { any: 0, nonstop: 1, "1stop": 2 };

  const seatType = seatMap[seat] || 1;
  const sortBy = sortMap[sort] || 2;
  const maxStops = stopsMap[stops] || 0;

  // Google Flights encoded filter format
  const encoded = encodeFlightRequest(origin, dest, date, seatType, sortBy, maxStops);

  const resp = await fetch(
    "https://www.google.com/_/FlightsFrontendUi/data/travel.frontend.flights.FlightsFrontendService/GetShoppingResults",
    {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded;charset=UTF-8" },
      body: `f.req=${encodeURIComponent(encoded)}`,
    }
  );

  const text = await resp.text();
  const cleaned = text.replace(/^\)\]\}'/, "");
  const parsed = JSON.parse(cleaned);

  if (!parsed?.[0]?.[2]) return [];
  const flightData = JSON.parse(parsed[0][2]);

  const flights = [];
  for (const idx of [2, 3]) {
    if (!Array.isArray(flightData?.[idx])) continue;
    const items = flightData[idx][0] || [];
    for (const item of items) {
      try {
        const flight = parseFlightItem(item, origin, dest);
        if (flight) flights.push(flight);
      } catch {}
    }
  }
  return flights;
}

function encodeFlightRequest(origin, dest, date, seat, sort, stops) {
  // Simplified encoding matching fli's approach
  return JSON.stringify([
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    [
      [
        null,
        null,
        seat,
        null,
        [],
        1, // adults
        null, null, null, null, null, null,
        [
          [
            [[origin, 0]],
            [[dest, 0]],
            date,
            null, null, null, null, null, null, null, null, null, null, null,
            stops || null,
          ],
        ],
      ],
      sort,
    ],
  ]);
}

function parseFlightItem(item, origin, dest) {
  if (!item) return null;
  const price = item?.[1]?.[1]?.[1] || item?.[1]?.[0]?.[1] || null;
  const duration = item?.[13]?.[0] || null;
  const stopsCount = item?.[13]?.[1] || 0;
  const legs = [];

  const legData = item?.[13]?.[2] || [];
  for (const leg of legData) {
    legs.push({
      airline: leg?.[2] || "Unknown",
      flight_number: leg?.[5] || "",
      dep_airport: leg?.[3] || origin,
      arr_airport: leg?.[4] || dest,
      dep_time: leg?.[0] || "",
      arr_time: leg?.[1] || "",
    });
  }

  return { origin, destination: dest, price, duration, stops: stopsCount, legs };
}

export const config = { path: "/api/search" };
