"""FlyLocal: Professional flight search. Mobile-first. Multi-origin.
Airline filters. Full airport autocomplete. GoWild checker. Powered by Google Flights."""
import json, time, os, sys, socket, random, html, re
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from datetime import datetime
from curl_cffi import requests as cf_requests

sys.path.insert(0, '/Volumes/Crucial/Users/mousears1090/projects/fli')
from fli.search import SearchFlights
from fli.models import (
    Airport, FlightSearchFilters, FlightSegment, PassengerInfo,
    SeatType, MaxStops, SortBy
)

# Build airport lookup: code → name
ALL_AIRPORTS = {a.name: a.value for a in Airport}

from fli.models import Airline
ALL_AIRLINES = {a.name: a.value for a in Airline}

searcher = SearchFlights()

# GoWild session — lazy init on first use
frontier_session = None

def get_frontier_session():
    global frontier_session
    if frontier_session is None:
        frontier_session = cf_requests.Session(impersonate="chrome")
    # Always refresh cookies
    try:
        frontier_session.get("https://www.flyfrontier.com", timeout=10)
    except:
        frontier_session = cf_requests.Session(impersonate="chrome")
        try:
            frontier_session.get("https://www.flyfrontier.com", timeout=10)
        except:
            pass
    return frontier_session

def check_gowild(origin, destination, date):
    """Check Frontier GoWild availability using curl_cffi (browser impersonation)."""
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        date_fmt = dt.strftime("%b-%d, %Y")  # "Apr-06, 2026"

        url = f"https://booking.flyfrontier.com/Flight/InternalSelect?o1={origin}&d1={destination}&dd1={date_fmt}&ADT=1&mon=true&promo="
        session = get_frontier_session()
        resp = session.get(url, timeout=15)

        if resp.status_code != 200:
            print(f"  GoWild {resp.status_code} for {origin}→{destination}")
            return []

        text = html.unescape(resp.text)
        start = text.find('{"journeys"')
        if start == -1:
            idx = text.find('"journeys"')
            if idx != -1:
                start = text.rfind('{', 0, idx)
        if start == -1:
            return []

        # Find matching closing brace
        depth = 0; end = start
        for i in range(start, min(start + 1000000, len(text))):
            if text[i] == '{': depth += 1
            elif text[i] == '}': depth -= 1
            if depth == 0: end = i + 1; break

        data = json.loads(text[start:end])
        gowild_flights = []

        for journey in data.get("journeys", []):
            for flight in journey.get("flights", []):
                if flight.get("isGoWildFareEnabled"):
                    gw_price = flight.get("goWildFare", flight.get("discountDenFare", 0))
                    duration = flight.get("duration", "")
                    stops_text = flight.get("stopsText", "")

                    # Extract flight number and times from legs
                    legs = flight.get("legs", [])
                    dep_time = ""
                    arr_time = ""
                    fnum = ""
                    if legs:
                        first_leg = legs[0]
                        last_leg = legs[-1]
                        dep_dt = first_leg.get("departureDate", "")
                        arr_dt = last_leg.get("arrivalDate", "")
                        fnum = first_leg.get("flightNumber", "")
                        # Format times nicely
                        if dep_dt:
                            try:
                                from datetime import datetime as _dt
                                dep_time = _dt.fromisoformat(dep_dt).strftime("%-I:%M %p")
                            except:
                                dep_time = dep_dt
                        if arr_dt:
                            try:
                                arr_time = _dt.fromisoformat(arr_dt).strftime("%-I:%M %p")
                            except:
                                arr_time = arr_dt

                    # Fallback: parse from fareKey
                    if not fnum or not dep_time:
                        fare_key = flight.get("goWildFareKey", flight.get("baseFareKey", ""))
                        if fare_key:
                            import re as _re
                            fk_match = _re.search(r'F9~(\d+)', fare_key)
                            if fk_match and not fnum:
                                fnum = fk_match.group(1)
                            time_match = _re.search(r'(\d{2}/\d{2}/\d{4}\s+\d+:\d+)~\w+~(\d{2}/\d{2}/\d{4}\s+\d+:\d+)', fare_key)
                            if time_match:
                                if not dep_time: dep_time = time_match.group(1)
                                if not arr_time: arr_time = time_match.group(2)

                    gowild_flights.append({
                        "origin": origin,
                        "destination": destination,
                        "price": gw_price,
                        "dep_time": dep_time,
                        "arr_time": arr_time,
                        "duration": duration,
                        "flight_number": f"F9 {fnum}",
                        "stops": stops_text,
                        "gowild": True,
                    })

        # Dedupe by flight number + price (same flight at same price = dupe)
        seen = set()
        filtered = []
        for f in gowild_flights:
            key = f"{f['flight_number']}_{f['dep_time']}_{f['price']}"
            if key in seen:
                continue
            seen.add(key)
            if f["price"] and f["price"] > 0:
                filtered.append(f)

        filtered.sort(key=lambda x: x["price"])
        return filtered
    except Exception as e:
        print(f"  GoWild check error: {e}")
        return []

def search_flights(origins, destination, date, stops="any", seat="economy", sort="cheapest"):
    seat_map = {"economy": SeatType.ECONOMY, "business": SeatType.BUSINESS,
                "first": SeatType.FIRST, "premium": SeatType.PREMIUM_ECONOMY}
    sort_map = {"cheapest": SortBy.CHEAPEST, "fastest": SortBy.DURATION,
                "best": SortBy.TOP_FLIGHTS}
    stops_map = {"any": None, "nonstop": MaxStops.NON_STOP,
                 "1stop": MaxStops.ONE_STOP_OR_FEWER, "2stops": MaxStops.TWO_OR_FEWER_STOPS}

    all_flights = []
    for origin in origins:
        try:
            origin_enum = Airport[origin.upper()]
            dest_enum = Airport[destination.upper()]
        except KeyError:
            continue

        filters = FlightSearchFilters(
            passenger_info=PassengerInfo(adults=1),
            flight_segments=[
                FlightSegment(
                    departure_airport=[[origin_enum, 0]],
                    arrival_airport=[[dest_enum, 0]],
                    travel_date=date,
                )
            ],
            seat_type=seat_map.get(seat, SeatType.ECONOMY),
            sort_by=sort_map.get(sort, SortBy.CHEAPEST),
        )
        if stops_map.get(stops):
            filters.stops = stops_map[stops]

        try:
            flights = searcher.search(filters, top_n=15)
            if flights:
                for f in flights:
                    all_flights.append({
                        "origin": origin.upper(),
                        "destination": destination.upper(),
                        "price": f.price,
                        "duration": f.duration,
                        "stops": f.stops,
                        "legs": [{
                            "airline": leg.airline.value if leg.airline else "Unknown",
                            "flight_number": leg.flight_number or "",
                            "dep_airport": leg.departure_airport.name if leg.departure_airport else origin,
                            "arr_airport": leg.arrival_airport.name if leg.arrival_airport else destination,
                            "dep_time": str(leg.departure_datetime) if leg.departure_datetime else "",
                            "arr_time": str(leg.arrival_datetime) if leg.arrival_datetime else "",
                        } for leg in (f.legs or [])],
                    })
        except Exception as e:
            print(f"  Error {origin}: {e}")

    all_flights.sort(key=lambda x: x["price"] or 999999)
    return all_flights


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>FlyLocal</title>
<style>
:root{--bg:#06080f;--s1:#0d1117;--s2:#161b26;--s3:#1c2333;--border:#252d3d;--text:#eceff4;--sub:#8892a4;--dim:#5c6578;--cyan:#22d3ee;--purple:#a78bfa;--green:#34d399;--orange:#fb923c;--red:#f87171;--blue:#60a5fa;--r:14px}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'SF Pro','Segoe UI',sans-serif;overflow-x:hidden}

/* Header */
.hdr{background:linear-gradient(160deg,#0c1220,#1a103a,#0c1220);padding:env(safe-area-inset-top,20px) 20px 20px;text-align:center;position:sticky;top:0;z-index:100;border-bottom:1px solid var(--border);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px)}
.hdr h1{font-size:26px;font-weight:800;background:linear-gradient(135deg,var(--cyan),var(--purple));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hdr p{font-size:12px;color:var(--dim);margin-top:2px}

/* Search Panel */
.panel{max-width:520px;margin:0 auto;padding:16px}
.section{margin-bottom:16px}
.section-label{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);font-weight:700;margin-bottom:8px;padding-left:4px}

/* Input Fields */
.input-wrap{position:relative}
.input-wrap input{width:100%;background:var(--s2);border:1.5px solid var(--border);border-radius:12px;padding:14px 16px;color:var(--text);font-size:16px;outline:none;transition:border .2s}
.input-wrap input:focus{border-color:var(--cyan)}
.input-wrap input::placeholder{color:var(--dim)}

/* Autocomplete Dropdown */
.ac-drop{position:absolute;top:100%;left:0;right:0;background:var(--s2);border:1px solid var(--border);border-radius:12px;margin-top:4px;max-height:200px;overflow-y:auto;z-index:50;display:none}
.ac-drop.show{display:block}
.ac-item{padding:12px 16px;font-size:14px;cursor:pointer;display:flex;justify-content:space-between;border-bottom:1px solid var(--border)}
.ac-item:last-child{border:none}
.ac-item:hover,.ac-item:active{background:var(--s3)}
.ac-item .code{font-weight:700;color:var(--cyan);font-size:15px}
.ac-item .name{color:var(--sub);font-size:13px}

/* Origin Tags */
.tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.tag{background:linear-gradient(135deg,var(--cyan)20,var(--purple)20);border:1px solid var(--cyan)40;color:var(--cyan);padding:6px 14px;border-radius:20px;font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px;animation:tagIn .2s ease}
.tag .x{width:18px;height:18px;border-radius:50%;background:rgba(255,255,255,0.1);display:flex;align-items:center;justify-content:center;font-size:12px;cursor:pointer}
@keyframes tagIn{from{transform:scale(0.8);opacity:0}to{transform:scale(1);opacity:1}}

/* Swap Button */
.swap-row{display:flex;gap:10px;align-items:flex-end}
.swap-row .input-wrap{flex:1}

/* Date & Filters */
.filter-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.filter-grid select,.filter-grid input[type=date]{width:100%;background:var(--s2);border:1.5px solid var(--border);border-radius:12px;padding:14px 12px;color:var(--text);font-size:15px;outline:none;-webkit-appearance:none;appearance:none}
.filter-grid select{background-image:url("data:image/svg+xml,%3Csvg width='10' height='6' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M0 0l5 6 5-6' fill='%235c6578'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 14px center}
input[type=date]::-webkit-calendar-picker-indicator{filter:invert(0.6)}

/* Airline Chips */

/* Search Button */
.search-btn{width:100%;background:linear-gradient(135deg,var(--cyan),var(--purple));color:#000;border:none;border-radius:14px;padding:16px;font-size:17px;font-weight:800;cursor:pointer;letter-spacing:0.3px;transition:transform .1s,opacity .15s;margin-top:8px}
.search-btn:active{transform:scale(0.98)}
.search-btn:disabled{opacity:0.4}

/* Results */
.results{max-width:520px;margin:0 auto;padding:0 16px 100px}
.res-bar{display:flex;justify-content:space-between;align-items:center;padding:16px 4px 12px}
.res-count{font-size:13px;color:var(--sub)}
.res-sort{font-size:12px;color:var(--dim)}

/* Flight Card */
.fcard{background:var(--s2);border:1.5px solid var(--border);border-radius:16px;padding:16px;margin-bottom:12px;transition:border-color .15s}
.fcard:active{border-color:var(--cyan)}
.fcard-top{display:flex;justify-content:space-between;align-items:flex-start}
.fcard-price{font-size:32px;font-weight:800;color:var(--green);line-height:1}
.fcard-origin{font-size:10px;font-weight:700;color:var(--purple);background:var(--purple)18;padding:3px 10px;border-radius:10px;margin-top:4px;display:inline-block;letter-spacing:0.5px}
.fcard-route{flex:1;padding-right:16px}
.fcard-airline{font-size:14px;font-weight:600;margin-bottom:2px}
.fcard-fnum{font-size:11px;color:var(--dim);margin-bottom:8px}
.fcard-times{display:flex;align-items:center;gap:8px}
.fcard-t{font-size:20px;font-weight:700;letter-spacing:-0.5px}
.fcard-mid{flex:1;display:flex;flex-direction:column;align-items:center}
.fcard-dur{font-size:11px;color:var(--sub);font-weight:600}
.fcard-line{width:100%;height:2px;background:var(--border);border-radius:1px;margin:4px 0;position:relative}
.fcard-line::after{content:'';position:absolute;right:-3px;top:-2px;width:6px;height:6px;border-radius:50%;background:var(--cyan)}
.fcard-stops{font-size:11px;font-weight:600}
.fcard-stops.ns{color:var(--green)}
.fcard-stops.s{color:var(--orange)}

.fcard-legs{margin-top:12px;padding-top:12px;border-top:1px solid var(--border)}
.fcard-leg{display:flex;justify-content:space-between;font-size:12px;color:var(--sub);padding:2px 0}

/* Loading */
.loading{text-align:center;padding:60px 20px}
.spinner{width:36px;height:36px;border:3px solid var(--border);border-top-color:var(--cyan);border-radius:50%;animation:spin .7s linear infinite;margin:0 auto 14px}
@keyframes spin{to{transform:rotate(360deg)}}
.loading p{color:var(--dim);font-size:14px}
.empty{text-align:center;padding:80px 20px;color:var(--dim);font-size:14px}
.gw-badge{display:inline-block;background:linear-gradient(135deg,#10b981,#06b6d4);color:#000;font-size:10px;font-weight:800;padding:3px 10px;border-radius:8px;letter-spacing:0.5px;margin-bottom:6px}
.gw-seats{font-size:11px;color:var(--orange);margin-top:4px}
</style>
</head>
<body>

<div class="hdr">
  <h1>FlyLocal</h1>
  <p>Real flights. Multiple origins. No tracking.</p>
</div>

<div class="panel">
  <div class="section">
    <div class="section-label">From</div>
    <div class="input-wrap">
      <input id="from-input" placeholder="Search airports..." autocomplete="off" oninput="acFrom(this.value)" onfocus="acFrom(this.value)" onkeydown="if(event.key==='Enter'){event.preventDefault();pickFirst('from-drop','origin')}">
      <div class="ac-drop" id="from-drop"></div>
    </div>
    <div class="tags" id="from-tags"></div>
  </div>

  <div class="section">
    <div class="section-label">To</div>
    <div class="input-wrap">
      <input id="to-input" placeholder="Search destination..." autocomplete="off" oninput="acTo(this.value)" onfocus="acTo(this.value)" onkeydown="if(event.key==='Enter'){event.preventDefault();pickFirst('to-drop','dest')}">
      <div class="ac-drop" id="to-drop"></div>
    </div>
    <div id="to-selected" style="margin-top:6px"></div>
  </div>

  <div class="section">
    <div class="section-label">Date & Preferences</div>
    <div class="filter-grid">
      <input type="date" id="date">
      <select id="stops"><option value="any">Any stops</option><option value="nonstop">Nonstop only</option><option value="1stop">1 stop max</option></select>
      <select id="seat"><option value="economy">Economy</option><option value="premium">Premium</option><option value="business">Business</option><option value="first">First</option></select>
      <select id="sort"><option value="cheapest">Cheapest</option><option value="fastest">Shortest</option><option value="best">Best overall</option></select>
    </div>
  </div>

  <div class="section">
    <div class="section-label">Airlines (optional)</div>
    <div class="input-wrap">
      <input id="al-input" placeholder="Search airlines..." autocomplete="off" oninput="acAl(this.value)" onfocus="acAl(this.value)" onkeydown="if(event.key==='Enter'){event.preventDefault();pickFirst('al-drop','airline')}">
      <div class="ac-drop" id="al-drop"></div>
    </div>
    <div class="tags" id="al-tags"></div>
  </div>

  <button class="search-btn" id="sbtn" onclick="doSearch()">Search Flights</button>
  <button class="search-btn" id="gwbtn" onclick="doGoWild()" style="background:linear-gradient(135deg,#10b981,#06b6d4);margin-top:8px">Check GoWild Availability</button>
</div>

<div class="results" id="results">
  <div class="empty">Select origins, destination, and date to search.</div>
</div>

<script>
const airports=AIRPORTS_JSON;
const airportKeys=Object.keys(airports);
const origins=new Set();
let dest='';
const selAirlines=new Set();

// Default date: 7 days out
const dd=new Date();dd.setDate(dd.getDate()+7);
document.getElementById('date').value=dd.toISOString().split('T')[0];

// Airlines autocomplete
const allAirlines=AIRLINES_JSON;
const alKeys=Object.keys(allAirlines);

function acAl(q){
  const drop=document.getElementById('al-drop');
  if(!q||q.length<1){drop.classList.remove('show');return}
  const qu=q.toUpperCase();
  const matches=alKeys.filter(k=>k.startsWith(qu)||allAirlines[k].toUpperCase().includes(qu)).slice(0,8);
  if(!matches.length){drop.classList.remove('show');return}
  drop.innerHTML=matches.map(k=>`<div class="ac-item" onmousedown="addAirline('${k}')"><span class="code">${k}</span><span class="name">${allAirlines[k]}</span></div>`).join('');
  drop.classList.add('show');
}
document.getElementById('al-input').addEventListener('blur',()=>setTimeout(()=>document.getElementById('al-drop').classList.remove('show'),150));

function addAirline(code){
  selAirlines.add(allAirlines[code]);
  document.getElementById('al-input').value='';
  document.getElementById('al-drop').classList.remove('show');
  renderAirlines();
}
function removeAirline(name){selAirlines.delete(name);renderAirlines()}
function renderAirlines(){
  document.getElementById('al-tags').innerHTML=[...selAirlines].map(a=>
    `<div class="tag" style="border-color:var(--purple);color:var(--purple)">${a} <div class="x" onclick="removeAirline('${a}')">&times;</div></div>`
  ).join('');
}

// Autocomplete
function pickFirst(dropId,type){
  const drop=document.getElementById(dropId);
  const first=drop.querySelector('.ac-item');
  if(first){first.dispatchEvent(new Event('mousedown'))}
}

function acSearch(q){
  if(!q||q.length<1)return [];
  const qu=q.toUpperCase();
  return airportKeys.filter(k=>k.startsWith(qu)||airports[k].toUpperCase().includes(qu)).slice(0,8);
}

function acFrom(q){
  const drop=document.getElementById('from-drop');
  const matches=acSearch(q);
  if(!matches.length){drop.classList.remove('show');return}
  drop.innerHTML=matches.map(k=>`<div class="ac-item" onmousedown="addOrigin('${k}')"><span class="code">${k}</span><span class="name">${airports[k]}</span></div>`).join('');
  drop.classList.add('show');
}

function acTo(q){
  const drop=document.getElementById('to-drop');
  const matches=acSearch(q);
  if(!matches.length){drop.classList.remove('show');return}
  drop.innerHTML=matches.map(k=>`<div class="ac-item" onmousedown="setDest('${k}')"><span class="code">${k}</span><span class="name">${airports[k]}</span></div>`).join('');
  drop.classList.add('show');
}

document.getElementById('from-input').addEventListener('blur',()=>setTimeout(()=>document.getElementById('from-drop').classList.remove('show'),150));
document.getElementById('to-input').addEventListener('blur',()=>setTimeout(()=>document.getElementById('to-drop').classList.remove('show'),150));

function addOrigin(code){
  origins.add(code);
  document.getElementById('from-input').value='';
  document.getElementById('from-drop').classList.remove('show');
  renderOrigins();
}

function removeOrigin(code){origins.delete(code);renderOrigins()}

function renderOrigins(){
  document.getElementById('from-tags').innerHTML=[...origins].map(c=>
    `<div class="tag">${c} <div class="x" onclick="removeOrigin('${c}')">&times;</div></div>`
  ).join('');
}

function setDest(code){
  dest=code;
  document.getElementById('to-input').value='';
  document.getElementById('to-drop').classList.remove('show');
  document.getElementById('to-selected').innerHTML=`<div class="tag">${code} &mdash; ${airports[code]||''} <div class="x" onclick="clearDest()">&times;</div></div>`;
}

function clearDest(){dest='';document.getElementById('to-selected').innerHTML=''}

async function doSearch(){
  if(!origins.size){document.getElementById('from-input').focus();return}
  if(!dest){document.getElementById('to-input').focus();return}
  const date=document.getElementById('date').value;
  if(!date)return;

  const btn=document.getElementById('sbtn');
  btn.disabled=true;btn.textContent='Searching '+origins.size+' airport(s)...';
  document.getElementById('results').innerHTML='<div class="loading"><div class="spinner"></div><p>Searching '+origins.size+' origin(s) to '+dest+'...</p></div>';

  try{
    const r=await fetch('/search',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({origins:[...origins],destination:dest,date,
        stops:document.getElementById('stops').value,
        seat:document.getElementById('seat').value,
        sort:document.getElementById('sort').value,
        airlines:[...selAirlines]})});
    let flights=await r.json();

    // Client-side airline filter
    if(selAirlines.size>0){
      flights=flights.filter(f=>(f.legs||[]).some(l=>selAirlines.has(l.airline)));
    }

    renderFlights(flights);
  }catch(e){
    document.getElementById('results').innerHTML='<div class="empty">Search failed. Try again.</div>';
  }
  btn.disabled=false;btn.textContent='Search Flights';
}

async function doGoWild(){
  if(!origins.size){document.getElementById('from-input').focus();return}
  if(!dest){document.getElementById('to-input').focus();return}
  const date=document.getElementById('date').value;
  if(!date)return;

  const btn=document.getElementById('gwbtn');
  btn.disabled=true;btn.textContent='Checking GoWild...';
  document.getElementById('results').innerHTML='<div class="loading"><div class="spinner"></div><p>Checking Frontier GoWild availability...</p></div>';

  let allGw=[];
  for(const o of origins){
    try{
      const r=await fetch('/gowild',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({origin:o,destination:dest,date})});
      const flights=await r.json();
      allGw=allGw.concat(flights);
    }catch(e){}
  }

  if(allGw.length===0){
    document.getElementById('results').innerHTML='<div class="empty">No GoWild flights found for this route/date. Try a different date or nearby airports.</div>';
  }else{
    let h='<div class="res-bar"><div class="res-count">'+allGw.length+' GoWild flights found</div></div>';
    allGw.forEach(f=>{
      const depT=f.dep_time||'--';
      const arrT=f.arr_time||'--';
      h+=`<div class="fcard" style="border-color:#10b98140">
        <div class="gw-badge">GOWILD</div>
        <div class="fcard-top">
          <div class="fcard-route">
            <div class="fcard-airline">Frontier Airlines</div>
            <div class="fcard-fnum">${f.flight_number||''}</div>
            <div class="fcard-times">
              <div class="fcard-t">${depT}</div>
              <div class="fcard-mid">
                <div class="fcard-dur">${f.duration||''}</div>
                <div class="fcard-line" style="background:var(--green)"></div>
                <div class="fcard-stops ns">Frontier GoWild</div>
              </div>
              <div class="fcard-t">${arrT}</div>
            </div>
            ${f.seats?'<div class="gw-seats">'+f.seats+' seats available</div>':''}
          </div>
          <div style="text-align:right">
            <div class="fcard-price">${f.price?'$'+f.price:'FREE'}</div>
            <div class="fcard-origin">${f.origin}</div>
          </div>
        </div>
      </div>`;
    });
    document.getElementById('results').innerHTML=h;
  }
  btn.disabled=false;btn.textContent='Check GoWild Availability';
}

function renderFlights(flights){
  const el=document.getElementById('results');
  if(!flights.length){el.innerHTML='<div class="empty">No flights found for these filters.</div>';return}

  let h='<div class="res-bar"><div class="res-count">'+flights.length+' flights found</div></div>';

  flights.forEach(f=>{
    const legs=f.legs||[];
    const first=legs[0]||{};
    const last=legs[legs.length-1]||first;
    const dt=first.dep_time?new Date(first.dep_time):null;
    const at=last.arr_time?new Date(last.arr_time):null;
    const depT=dt?dt.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):'--';
    const arrT=at?at.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):'--';
    const hrs=Math.floor((f.duration||0)/60);
    const mins=(f.duration||0)%60;
    const stopT=f.stops===0?'Nonstop':f.stops+' stop'+(f.stops>1?'s':'');
    const stopC=f.stops===0?'ns':'s';
    const airlines=[...new Set(legs.map(l=>l.airline))].join(', ');
    const fnums=legs.map(l=>l.flight_number).filter(Boolean).join(' \u2192 ');

    h+=`<div class="fcard">
      <div class="fcard-top">
        <div class="fcard-route">
          <div class="fcard-airline">${airlines}</div>
          <div class="fcard-fnum">${fnums}</div>
          <div class="fcard-times">
            <div class="fcard-t">${depT}</div>
            <div class="fcard-mid">
              <div class="fcard-dur">${hrs}h ${mins}m</div>
              <div class="fcard-line"></div>
              <div class="fcard-stops ${stopC}">${stopT}</div>
            </div>
            <div class="fcard-t">${arrT}</div>
          </div>
        </div>
        <div style="text-align:right">
          <div class="fcard-price">$${f.price||'--'}</div>
          <div class="fcard-origin">${f.origin}</div>
        </div>
      </div>
      ${legs.length>1?'<div class="fcard-legs">'+legs.map(l=>`<div class="fcard-leg"><span>${l.airline} ${l.flight_number}</span><span>${l.dep_airport} \u2192 ${l.arr_airport}</span></div>`).join('')+'</div>':''}
    </div>`;
  });
  el.innerHTML=h;
}
</script>
</body>
</html>"""

# Inject data
HTML = HTML.replace('AIRPORTS_JSON', json.dumps(ALL_AIRPORTS))
HTML = HTML.replace('AIRLINES_JSON', json.dumps(ALL_AIRLINES))

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        if self.path in ('/search', '/api/search'):
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                print(f"Search: {body.get('origins')} → {body.get('destination')} on {body.get('date')}")
                flights = search_flights(
                    body.get('origins', []), body.get('destination', ''),
                    body.get('date', ''), body.get('stops', 'any'),
                    body.get('seat', 'economy'), body.get('sort', 'cheapest'))
                print(f"  Found {len(flights)} flights")
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(json.dumps(flights).encode())
            except Exception as e:
                print(f"  Error: {e}")
                import traceback; traceback.print_exc()
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())

        if self.path in ('/gowild', '/api/gowild'):
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length))
                # Support both single origin and multiple origins
                origins = body.get('origins', [])
                if not origins:
                    origin = body.get('origin', '')
                    if origin: origins = [origin]
                dest = body.get('destination', '')
                date = body.get('date', '')
                flights = []
                for o in origins:
                    print(f"GoWild check: {o} → {dest} on {date}")
                    result = check_gowild(o, dest, date)
                    flights.extend(result)
                    if len(origins) > 1:
                        import time as _t; _t.sleep(2)
                flights.sort(key=lambda x: x.get("price", 999))
                print(f"  Found {len(flights)} GoWild flights total")
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(json.dumps(flights).encode())
            except Exception as e:
                print(f"  GoWild error: {e}")
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'[]')

    def log_message(self, *a): pass

print("FlyLocal at http://localhost:8877")
server = ThreadingHTTPServer(('127.0.0.1', 8877), Handler)
server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.serve_forever()
