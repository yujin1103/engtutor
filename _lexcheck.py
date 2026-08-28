from app.content import lexicon
ws = ["quiet","brief","aid","pop","delay","trend","desire","safety","novel","union",
      "unionize","combine","group","association","postpone","nowadays","once","double",
      "technician","scientist","crisis","crises","settle","solve","attract","interest",
      "necessary","essential","subject","topic","fresh","new","twice","currently","now",
      "seemingly","unfortunately","apparently","mostly","usually","stove","teller","cashier"]
print("available:", lexicon.available())
for w in ws:
    print(f"{w:14} known={lexicon.known(w)} pos={lexicon.parts_of_speech(w)}")
