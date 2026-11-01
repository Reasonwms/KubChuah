"""Builds tests/sample-results.csv: FAKE businesses in the exact gosom CSV format. Test data only."""
import csv, json, sys
H = ["input_id","link","title","category","address","open_hours","popular_times","website","phone","plus_code","review_count","review_rating","reviews_per_rating","latitude","longitude","cid","status","descriptions","reviews_link","thumbnail","timezone","price_range","data_id","street_view_url","place_id","images","reservations","order_online","menu","owner","complete_address","credit_cards_accepted","about","user_reviews","user_reviews_extended","emails"]
hours = json.dumps({"Monday":["8 AM-5 PM"]}); imgs = json.dumps([{"title":"a","image":"x"}]*5)
def row(pid, title, cat, city, site, phone, rc, rt, emails="", desc="Family owned", h=hours, im=imgs, status="Open ⋅ Closes 5 PM", owner=True):
    return {"input_id":"test","link":f"https://maps.example/{pid}","title":title,"category":cat,
      "address":f"123 Test St, {city}, MI 48091","open_hours":h,"website":site,"phone":phone,
      "review_count":str(rc),"review_rating":f"{rt:.6f}","status":status,"descriptions":desc,"place_id":pid,
      "images":im,"owner":json.dumps({"id":"1","name":"Owner","link":""} if owner else {"id":"","name":"","link":""}),
      "complete_address":json.dumps({"city":city,"state":"MI"}),"emails":emails}
rows = [
 row("p1","FAKE Ridge Roofing","Roofing contractor","Warren","","(586) 555-0101",4,4.8,"",desc="",h="null",im="[]",owner=False),
 row("p2","FAKE Summit Roofing","Roofing contractor","Warren","https://fakesummitroof.example","(586) 555-0102",310,4.7,"info@fakesummitroof.example"),
 row("p3","FAKE Lakeside Roofing","Roofing contractor","Eastpointe","https://www.facebook.com/fakelakeside","(586) 555-0103",22,4.6,""),
 row("p4","FAKE Bright Smile Dental","Dentist","Eastpointe","https://fakebrightsmile.example","(586) 555-0104",35,4.9,"office@fakebrightsmile.example"),
 row("p5","FAKE Macomb Family Dental","Dentist","Warren","https://fakemacombdental.example","(586) 555-0105",412,4.8,"hello@fakemacombdental.example"),
 row("p6","Jiffy Lube FAKE","Oil change service","Warren","https://jiffylube.example","(586) 555-0106",900,4.1),
 row("p7","FAKE Corner Cafe","Restaurant","Warren","","(586) 555-0107",60,4.3),
 row("p8","FAKE Gone Plumbing","Plumber","Warren","https://fakegone.example","(586) 555-0108",15,4.0,"x@fakegone.example",status="Permanently closed"),
 row("p9","FAKE Ghost HVAC","HVAC contractor","Eastpointe","","",2,5.0),
 row("p10","FAKE Eastside Plumbing","Plumber","Eastpointe","https://fakeeastside.example","(586) 555-0110",9,3.6,"jobs@fakeeastside.example"),
 row("p1","FAKE Ridge Roofing","Roofing contractor","Warren","","(586) 555-0101",4,4.8),  # duplicate from second query
 row("p11","FAKE Detroit Roofing","Roofing contractor","Detroit","","(313) 555-0111",3,4.9),  # outside target cities
]
w = csv.DictWriter(open(sys.argv[1],"w",newline=""), fieldnames=H, restval=""); w.writeheader(); w.writerows(rows)
