# სწრაფი დაყენება — Georgian Painter Collector

ეს პროექტი საათში ერთხელ ეძებს ერთ ახალ ქართველ მხატვარს, ამოწმებს Wikimedia Commons-ის ლიცენზიას, რეალურად ტვირთავს JPG/PNG ფაილებს და ქმნის ZIP არქივს.

## დაყენება

1. GitHub-ზე შექმენი ახალი ცარიელი repository, მაგალითად `georgian-painter-collector`.
2. ამ ZIP-ის შიგთავსი მთლიანად ატვირთე repository-ის root-ში. `.github` საქაღალდეც აუცილებლად უნდა აიტვირთოს.
3. გახსენი **Actions** და ჩართე workflow-ები, თუ GitHub ამას მოგთხოვს.
4. თუ history-ის commit-ზე permission error გამოვიდა: **Settings → Actions → General → Workflow permissions → Read and write permissions**.
5. პირველი შემოწმებისთვის: **Actions → Georgian Painter Collector → Run workflow**.

ამის შემდეგ workflow გაეშვება საათში ერთხელ, თბილისის დროის სარტყლით, ყოველ საათში :17 წუთზე.

## სად იქნება ZIP

**Actions → შესაბამისი run → Artifacts**. იქ გამოჩნდება კონკრეტული მხატვრის არქივი.

არქივში არის თვითონ JPG/PNG სურათები, `SOURCES.txt` და `manifest.json`.

მხატვარი `state/history.json`-ში ჩაიწერება მხოლოდ მაშინ, თუ მინიმუმ 8 რეალური სურათი წარმატებით ჩამოიტვირთა და საბოლოო ZIP-იც შემოწმდა.
