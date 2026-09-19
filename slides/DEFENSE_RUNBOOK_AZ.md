# Müdafiə slaydları üçün geniş danışıq və izah sənədi

Bu sənəd 19 slayddan ibarət Azərbaycan dilli təqdimatla birlikdə istifadə olunur. Hər slayd üçün material üç ardıcıl hissəyə bölünüb:

1. **Danışılacaq mətn** — təqdimat zamanı sözbəsöz deyilə biləcək geniş mətn.
2. **Prosesin izahı** — mətndə təsvir edilən əməliyyatların sadə dildə, addım-addım açıqlaması.
3. **Terminlərin izahı** — həmin slaydda işlənən texniki sözlərin qısa və dəqiq mənası.

Danışıq mətni neytral və əsasən şəxssiz üslubda yazılıb. Elmin995 və fatimekazimli öz bloklarında texniki mexanizmi izah edirlər; bu bölgü həmin kodun müəllifliyinin onlara aid olması demək deyil. Faktiki müəlliflik və töhfələr 19-cu slaydda ayrıca göstərilir.

| Slaydlar | Danışan | Mövzu |
|---:|---|---|
| 1–6 | ii1ahe | Problem, məlumat axını, arxitektura və istinad yoxlaması |
| 7–11 | Elmin995 | Asinxron icra, timeout, retry, keş və AI inteqrasiyası |
| 12–16 | fatimekazimli | PostgreSQL, işə salınma, testlər, benchmark və reproduksiya |
| 17–19 | ii1ahe; 19-da hər üç üzv | Canlı nəticə, məhdudiyyətlər və faktiki töhfələr |

## 1. Asinxron tədqiqat köməkçisi

### Danışılacaq mətn

“Təqdim edilən layihə asinxron tədqiqat köməkçisidir. Sistemə bir tədqiqat sualı verilir və həmin sual üzrə Wikipedia, arXiv və açıq veb axtarışından material toplanır. Toplanmış material bir siyahıda birləşdirilir, təkrarlanan mənbələr çıxarılır və süni intellekt vasitəsilə qısa cavab hazırlanır. Cavabın içində istifadə olunan istinad nömrələri ayrıca mənbə siyahısındakı qeydlərlə əlaqələndirilir.

Burada əsas məqsəd sadəcə mətn yaratmaq deyil. Cavabın hansı materiallara əsaslandığının görünməsi, ayrı-ayrı mənbələrin uğurlu və ya uğursuz olmasının qeyd edilməsi, prosesin vaxtının ölçülməsi və tamamlanmış sessiyanın lazım gəldikdə bazada saxlanması da sistemin nəticəsinə daxildir. Buna görə layihə birbaşa modelə sual verən sadə proqramdan daha genişdir. Model yalnız sintez mərhələsində istifadə olunur; mənbələrin seçilməsi, paralel toplanması, xəta siyasəti, keş və saxlanma tətbiqin öz qatlarında idarə edilir.

Asinxron yanaşma ona görə seçilib ki, üç mənbəyə göndərilən şəbəkə sorğuları vaxtının böyük hissəsini cavab gözləməklə keçirir. Bir mənbənin cavabı gözlənilərkən digər mənbənin sorğusu da davam etdirilə bilir. Nəticədə mənbə toplama mərhələsinin vaxtı azaldılır. Təqdimat boyunca bu axının kodda necə qurulduğu, hansı hallarda qismən cavab qaytarıldığı, nəticələrin necə yoxlandığı və ölçmələrin nə göstərdiyi izah ediləcək.”

### Oxuyucu üçün prosesin izahı

Bu slayd bütün sistemin qısa xəritəsidir. Proses belə başa düşülməlidir:

1. İstifadəçidən bir sual qəbul edilir.
2. Sual eyni vaxtda bir neçə məlumat mənbəyinə göndərilir.
3. Hər mənbədən başlıq, keçid və qısa mətn hissələri alınır.
4. Eyni keçid iki mənbədən gəlibsə, təkrar nüsxə çıxarılır.
5. Qalan mənbələr sabit sıra ilə AI modelinə verilir.
6. Model cavab hazırlayır və mənbələrə nömrə ilə istinad edir.
7. Cavabın boş olmaması və nömrələrin mənbə siyahısı ilə uyğunluğu yoxlanılır.
8. Nəticə istifadəçiyə göstərilir və sazlamadan asılı olaraq PostgreSQL-də saxlanılır.

“Asinxron” sözü bütün addımların eyni anda yerinə yetirilməsi demək deyil. Burada əsas paralellik uzaq mənbələrdən cavab gözlənilən hissədə tətbiq edilir. Sintez isə bütün mənbələr hazırlandıqdan sonra bir model çağırışı ilə aparılır.

### Terminlərin izahı

- **Tədqiqat sualı:** İstifadəçinin cavablandırılmasını istədiyi mətn.
- **Mənbə:** Cavab üçün material verən Wikipedia səhifəsi, arXiv məqaləsi və ya veb nəticəsi.
- **Asinxron icra:** Bir şəbəkə cavabı gözlənilərkən başqa gözləyən işlərin də irəli aparılması.
- **Sintez:** Müxtəlif mənbələrdəki materialdan vahid cavab hazırlanması.
- **İstinad:** Cavabdakı iddianın hansı nömrəli mənbəyə bağlandığını göstərən işarə.
- **Sessiya:** Bir sualın giriş məlumatı, mənbə nəticələri, cavabı, vaxtları və statusları ilə birlikdə tam icra qeydi.

## 2. Cavabın mənbələrinin göstərilməsi

### Danışılacaq mətn

“Sistemin həll etdiyi əsas problem cavabın mənbələrinin görünməsidir. Məsələn, ‘Fotosintez nədir və əsas mərhələləri hansılardır?’ sualına təkcə səlis bir abzas qaytarıla bilər. Lakin həmin abzasın hansı materiala əsaslandığı göstərilmədikdə istifadəçi nəticəni ayrıca yoxlaya bilmir. Buna görə eyni sual üzrə üç fərqli mənbə növündən material toplanır.

Wikipedia ümumi anlayışın və terminlərin izahı üçün yararlı ola bilər. arXiv elmi məqalələrin preprint versiyalarını təqdim edir. Veb axtarışı isə digər açıq səhifələri tapır. Bu mənbələr keyfiyyət baxımından eyni hesab edilmir və onların mövcudluğu cavabın avtomatik olaraq doğru olduğunu göstərmir. Məqsəd müxtəlif materialların toplanması və istifadə olunan mənbələrin istifadəçiyə açıq göstərilməsidir.

Slaydda görünən [1], [7] və [8] işarələri 18 sentyabrda aparılmış canlı yoxlamada cavabda qeydə alınan istinadlardır. Hər nömrə son mənbə siyahısındakı mövqeyə bağlıdır. Məsələn, [7] işarəsi yeddinci mənbəni göstərir. Bu əlaqə sayəsində istifadəçi cavabdakı istinadı mənbə siyahısında tapıb həmin səhifəni aça bilər. Bununla belə, düzgün nömrənin göstərilməsi mənbənin bütün iddianı tam təsdiqlədiyini sübut etmir. Sistem istinad strukturunu yoxlayır, faktın elmi cəhətdən düzgünlüyünü isə ayrıca qiymətləndirmir.”

### Oxuyucu üçün prosesin izahı

Bir mənbədən istifadə edildikdə həmin mənbənin boş, köhnə və ya mövzuya zəif uyğun olması riski artır. Üç mənbə növünün seçilməsi bu riski tam aradan qaldırmır, lakin cavab üçün daha geniş material sahəsi yaradır.

İstinadların yaranması belə baş verir:

1. Toplanmış mənbələr sabit siyahıya salınır.
2. Siyahının birinci elementi 1, ikinci elementi 2 və davamı üzrə nömrələnir.
3. Həmin sıralanmış siyahı AI sintezinə göndərilir.
4. Model cavabda istifadə etdiyi mövqeləri kvadrat mötərizədə göstərir.
5. Cavab qaytarıldıqdan sonra istinad obyektinin göstərdiyi mənbə ilə siyahıdakı mənbə müqayisə edilir.

Bu səbəbdən sintezdən sonra mənbə sırasının dəyişdirilməsi təhlükəlidir. Siyahı dəyişdirilsə, cavabdakı [1] əvvəlki mənbə əvəzinə başqa səhifəyə yönələ bilər.

### Terminlərin izahı

- **Wikipedia:** Ümumi ensiklopedik məlumat mənbəyi.
- **arXiv:** Elmi məqalələrin, çox vaxt rəsmi jurnal nəşrindən əvvəlki versiyalarının saxlandığı açıq platforma.
- **Veb axtarışı:** Axtarış provayderi vasitəsilə açıq internet səhifələrinin tapılması.
- **Preprint:** Elmi məqalənin ekspert qiymətləndirməsindən əvvəl və ya paralel yayımlanan versiyası.
- **İstinad indeksi:** Mənbə siyahısındakı mövqeni göstərən müsbət tam ədəd.
- **İzlənəbilənlik:** Nəticənin hansı girişdən və hansı mənbədən yarandığını sonradan müəyyən etmək imkanı.

## 3. Giriş və çıxış müqaviləsi

### Danışılacaq mətn

“Tətbiq daxilində məlumatın hansı formada qəbul və qaytarılması modellərlə müəyyən edilir. Giriş tərəfdə ResearchRequest adlı model istifadə olunur. Bu modeldə sual mətni, seçilmiş mənbələrin sıralı dəsti, keşdən istifadə icazəsi və hər mənbədən istənən maksimum nəticə sayı saxlanılır. Belə bir modelin istifadəsi ayrı-ayrı funksiyalara əlaqəsiz dəyişənlər ötürülməsinin qarşısını alır və bütün sorğular üçün eyni qaydaların tətbiq edilməsinə imkan verir.

Sual boş ola bilməz. Ən azı bir mənbə seçilməlidir. Eyni mənbənin iki dəfə göstərilməsinə icazə verilmir. Nəticə sayı müsbət olmalıdır və sazlamada müəyyən edilmiş yuxarı həddi keçməməlidir. Bu qaydaların bir hissəsi Pydantic modelində, sazlamadan asılı hissəsi isə ayrıca validation funksiyasında yoxlanılır.

Çıxış tərəfdə yalnız cavab mətni qaytarılmır. Hər mənbənin uğurlu, boş, vaxtı aşmış və ya xətalı olması ayrıca göstərilir. Cavab yaradılıbsa, nömrəli istinadlar verilir. Sessiyanın bazaya yazılıb-yazılmaması da nəticədə qeyd olunur. Terminal çıxış kodu avtomatlaşdırma üçün ayrıca məna daşıyır: sıfır uğurlu və ya qismən uğurlu cavabı; bir cavabın yaradılmamasını və ya tələb olunan saxlamanın alınmamasını; iki isə istifadəçi girişi və sazlama xətasını bildirir.”

### Oxuyucu üçün prosesin izahı

“Müqavilə” sözü burada hüquqi sənəd deyil. Bir modulun başqa modula hansı sahələri verəcəyini və qarşılığında hansı məlumatı alacağını bildirir. Məlumatın bu formada sabitləşdirilməsi üç fayda verir:

1. Yanlış giriş erkən mərhələdə aşkarlanır.
2. Funksiyalar hansı sahələrin mövcud olduğunu əvvəlcədən bilir.
3. Testlər eyni model üzərindən normal və səhv halları qura bilir.

Slayddakı sahələrin mənası:

- **question** istifadəçinin orijinal sualını saxlayır.
- **sources** seçilən mənbələri və onların sırasını saxlayır.
- **use_cache** əvvəlki mənbə nəticəsinin oxunmasına icazə verib-verməməyi göstərir.
- **max_results** hər mənbədən ən çox neçə nəticə istənəcəyini müəyyən edir.

Çıxış kodu ekranda göstərilən statusdan fərqli məqsədə xidmət edir. Status insan üçün məna verir; rəqəm isə shell skripti, Docker və ya CI kimi başqa proqramlara icranın nəticəsini bildirir.

### Terminlərin izahı

- **Məlumat modeli:** Müəyyən obyektin sahələrini və onların tiplərini təsvir edən struktur.
- **Pydantic:** Python məlumat modellərini qurmaq və daxil olan məlumatı yoxlamaq üçün kitabxana.
- **Tuple:** Sırası qorunan və yaradıldıqdan sonra dəyişdirilməsi nəzərdə tutulmayan elementlər dəsti.
- **Validation:** Daxil olan və ya yaradılan məlumatın qaydalara uyğunluğunun yoxlanması.
- **Konfiqurasiya və ya sazlama:** Kod dəyişdirilmədən mühit dəyişənləri ilə seçilən limit və provayder məlumatları.
- **Çıxış kodu:** Proses bitdikdə əməliyyat sisteminə qaytarılan tam ədəd.

## 4. Sorğunun emal mərhələləri

### Danışılacaq mətn

“Bir sorğunun nəticəyə çevrilməsi altı əsas mərhələyə bölünür. Birinci mərhələdə istifadəçi girişi yoxlanılır və keş üçün kanonik sual forması hazırlanır. Kanonik forma orijinal sualı əvəz etmir; yalnız eyni mənalı yazılışların keşdə eyni açara düşməsi üçün istifadə olunur.

İkinci mərhələdə seçilmiş mənbələrə sorğular göndərilir. Bu sorğular semaforla məhdudlaşdırılmış asinxron icra altında işlədilir. Üçüncü mərhələdə uğurla alınmış materiallar mənbə sırasına uyğun birləşdirilir və eyni URL-ə aid təkrar nəticələr çıxarılır. İlk rast gəlinən nüsxə saxlanılır. Beləliklə, nömrələmə üçün sabit mənbə siyahısı əldə edilir.

Dördüncü mərhələdə həmin siyahı və istifadəçinin orijinal sualı AI sintezinə ötürülür. Beşinci mərhələdə cavabın boş olmaması, istinad indekslərinin müsbət və sərhəd daxilində olması, təkrarlanmaması və uyğun mənbə obyektinə işarə etməsi yoxlanılır. Altıncı mərhələdə bütün məlumat ResearchSession obyektində birləşdirilir. Sazlama icazə verirsə sessiya PostgreSQL-ə yazılır, sonra cavab və diaqnostika terminala çıxarılır.

Bu mərhələlərdən birinin uğursuzluğu həmişə bütün prosesi ləğv etmir. Məsələn, bir mənbə vaxtı aşdıqda digər iki mənbə ilə cavab yaradıla bilər. Keş oxusu alınmadıqda canlı axtarışa keçilə bilər. Sessiya yazısı alınmadıqda cavab istifadəçiyə göstərilir, lakin saxlanmama statusu və qeyri-sıfır çıxış kodu verilir.”

### Oxuyucu üçün prosesin izahı

Altı mərhələni üç böyük hissə kimi yadda saxlamaq olar:

1. **Hazırlıq:** giriş yoxlanılır və sorğunun keş forması hazırlanır.
2. **Tədqiqat və cavab:** mənbələr toplanır, təkrarlar silinir, cavab sintez edilir və istinadlar yoxlanılır.
3. **Nəticənin tamamlanması:** vaxtlar, statuslar və xəbərdarlıqlar sessiyada birləşdirilir; saxlama və terminal çıxışı icra edilir.

Kanonik sual forması ilə orijinal sualın fərqi vacibdir. Məsələn, artıq boşluqlar və böyük-kiçik hərf fərqi keş açarında normallaşdırıla bilər. Lakin modelə istifadəçinin yazdığı orijinal sual verilir ki, məna və ifadə tərzi itirilməsin.

### Terminlərin izahı

- **Kanonik forma:** Müxtəlif yazılışları vahid müqayisə formasına gətirən mətn.
- **URL deduplikasiyası:** Eyni keçidə aid təkrar nəticələrin çıxarılması.
- **Orchestrator:** Bir neçə xidmət çağırışının sırasını və nəticələrinin birləşdirilməsini idarə edən modul.
- **ResearchSession:** Tam icra məlumatını saxlayan model.
- **Diaqnostika:** İcra zamanı mənbələrin vəziyyəti, xəbərdarlıqlar və vaxtlar barədə əlavə məlumat.
- **Qismən uğur:** Bütün mənbələr işləməsə də istifadə edilə bilən cavabın yaradıldığı vəziyyət.

## 5. Sistemin qatlara bölünməsi

### Danışılacaq mətn

“Layihə qatlı modul monolit kimi qurulub. Bu o deməkdir ki, tətbiq bir proses və bir kod bazası kimi işləyir, lakin məsuliyyətlər ayrı modullarda saxlanılır. Sistem mikroservislərə bölünməyib və modullar arasında şəbəkə çağırışı yoxdur.

Ən yuxarıda CLI və bootstrap yerləşir. CLI terminal arqumentlərini qəbul edir və nəticəni istifadəçiyə göstərir. Bootstrap sazlamanı oxuyur, HTTP client-i, lazım gəldikdə PostgreSQL pool-unu və xidmət obyektlərini yaradır. Bütün obyekt qrafını bilən əsas modul bootstrap-dır. Digər modullar ehtiyac duyduqları asılılıqları hazır obyekt kimi qəbul edir.

ResearchService əsas istifadə halını idarə edir: mənbələrin toplanması başladılır, material olduqda sintez çağırılır, nəticə sessiya obyektinə çevrilir və saxlama icra edilir. Aşağıdakı xidmət qatında Orchestrator, CacheService və AIService yerləşir. Orchestrator mənbələrin paralel toplanmasını və nəticə sırasını idarə edir. CacheService keş oxu və yazı xətalarını tətbiq səviyyəsində idarə olunan nəticəyə çevirir. AIService verilmiş AI paketinə edilən çağırışları, retry, timeout və xəta tərcüməsini bir sərhəddə saxlayır.

Storage Protocols keş və sessiya saxlaması üçün tələb olunan metodları təsvir edir. Bu müqavilələrin həm PostgreSQL, həm də yaddaş implementasiyası mövcuddur. Verilmiş ai qovluğu dəyişdirilməyib. Tətbiqin ona çıxışı əsasən AIService vasitəsilə edilir; Wikipedia fulltext axtarışı isə sənədləşdirilmiş istisna kimi birbaşa MediaWiki API-yə gedir.”

### Oxuyucu üçün prosesin izahı

Qatların ayrılması bir funksiyanın hər işi görməsinin qarşısını alır. Məsələn:

- CLI bazaya birbaşa SQL yazmır.
- ResearchService Google və ya OpenAI SDK xətalarını tanımır.
- Orchestrator terminala mətn çap etmir.
- PostgreSQL repository-si cavab sintez etmir.

Bu ayrılıq testləri də asanlaşdırır. ResearchService yoxlanarkən real PostgreSQL əvəzinə yaddaş implementasiyası, real AI əvəzinə saxta xidmət verilə bilər. Modul yalnız müqavilədən asılı olduğuna görə altdakı konkret texnologiyanın dəyişdirilməsi daha az yerə təsir edir.

### Terminlərin izahı

- **Modul monolit:** Bir tətbiq kimi yerləşdirilən, daxildə isə aydın modullara bölünən sistem.
- **Qat:** Eyni növ məsuliyyətləri daşıyan proqram hissəsi.
- **CLI:** Command Line Interface; proqramın terminaldan idarə olunan giriş səthi.
- **Bootstrap və ya composition root:** Asılılıqların yaradıldığı və bir-birinə bağlandığı yer.
- **Dependency injection:** Modulun asılılığını özü yaratmaq əvəzinə hazır qəbul etməsi.
- **Protocol:** Obyektin hansı metodları təqdim etməli olduğunu təsvir edən struktur müqavilə.
- **Implementasiya:** Müqavilədə göstərilən davranışı faktiki yerinə yetirən konkret kod.

## 6. İstinad nömrələrinin yoxlanması

### Danışılacaq mətn

“AI modelindən cavab alındıqdan sonra nəticə birbaşa uğurlu hesab edilmir. Əvvəlcə cavabın mətni yoxlanılır. Slaydda göstərilən real kod fraqmentində answer sahəsinə strip tətbiq edilir. Strip başlanğıc və sondakı boşluqları çıxarır. Bundan sonra mətn boş qalırsa InvalidAnswerError yaradılır. Beləliklə, modelin boş sətir və ya yalnız boşluq qaytardığı hal uğurlu cavab kimi göstərilmir.

Sonra hər istinad ayrıca yoxlanılır. İstinad nömrəsi bir və ya daha böyük olmalıdır, çünki siyahı istifadəçiyə birinci mənbədən başlayaraq göstərilir. Nömrə mənbə siyahısının ölçüsünü keçməməlidir. Eyni indeks iki dəfə ayrıca citation obyekti kimi verilməməlidir. Ən vacib yoxlama istinad obyektində saxlanan mənbənin məhz sources[index minus one] mövqeyindəki mənbə ilə eyni olmasıdır. Python siyahısı sıfırdan, istifadəçiyə göstərilən istinad isə birdən başladığı üçün bir vahid çıxılır.

Bu yoxlama istinadların daxili uyğunluğunu qoruyur. Məsələn, [7] işarəsi yeddinci mənbəyə bağlı qalır və siyahı sonradan dəyişdirilibsə uyğunsuzluq aşkarlanır. Lakin burada mətnin semantik doğruluğu ölçülmür. Mənbədə fotosintezin işıq mərhələsi haqqında məlumat olması və cavabdakı konkret cümlənin həmin məlumatla tam dəstəklənməsi ayrıca məzmun yoxlaması tələb edir. Mövcud sistem həmin iddia-mənbə uyğunluğunu avtomatik qiymətləndirmir.”

### Oxuyucu üçün prosesin izahı

Yoxlama iki səviyyəyə ayrılır:

1. **Mövcudluq yoxlaması:** Cavabda oxuna bilən mətn varmı?
2. **Struktur yoxlaması:** İstinad nömrələri mövcud mənbələrə düzgün bağlanıbmı?

Üçüncü mümkün səviyyə fakt və ya grounding yoxlaması olardı. Bu səviyyədə cavabdakı hər iddia seçilir, istinad edilən mənbənin mətni ilə müqayisə olunur və dəstəyin kifayət edib-etmədiyi qiymətləndirilir. Layihədə bu hissə yoxdur və məhdudiyyət kimi ayrıca göstərilir.

Kod fraqmentində xəta yaradılması proqramın çökməsi demək deyil. AIService bu tip xətanı öz xəta siyasətinə uyğun yuxarı qata ötürür; ResearchService sintez uğursuzluğunu nəticə statusuna və xəbərdarlığa çevirir.

### Terminlərin izahı

- **strip:** Mətnin əvvəl və sonundakı boşluq və sətirsonu simvollarını çıxaran əməliyyat.
- **InvalidAnswerError:** AI tərəfindən qaytarılmış nəticə tətbiqin cavab qaydalarına uyğun olmadıqda istifadə olunan xəta.
- **İndeks:** Elementin siyahıdakı mövqeyi.
- **Citation obyekti:** İstinad nömrəsini və ona bağlı mənbəni birlikdə saxlayan məlumat obyekti.
- **Daxili uyğunluq:** Bir nəticənin öz hissələrinin bir-biri ilə ziddiyyət təşkil etməməsi.
- **Semantik doğruluq:** Mətnin mənaca və fakt baxımından doğru olması.
- **Grounding:** Cavabdakı iddianın təqdim edilmiş mənbə materialı ilə dəstəklənməsi.

## 7. Mənbə sorğularının paralel icrası

### Danışılacaq mətn

“Wikipedia, arXiv və veb axtarışı uzaq şəbəkə xidmətləridir. Sorğu göndərildikdən sonra proqramın vaxtının böyük hissəsi serverdən cavab gözləməklə keçir. Bu müddətdə prosessor davamlı hesablama aparmır. Ona görə mənbələrin bir-birinin ardınca gözlənilməsi əvəzinə asinxron şəkildə birlikdə başladılması seçilib.

Slayddakı zolaqlar konkret benchmark rəqəmi deyil; iş prinsipini göstərən sxemdir. Üç sorğu yaxın vaxtda başladılır. Veb cavabı tez, arXiv daha gec, Wikipedia isə ən gec qaytara bilər. Ümumi gözləmə vaxtı bu halda üç müddətin cəminə deyil, əsasən ən gec tamamlanan sorğunun müddətinə yaxınlaşır.

Paralel icra nəzarətsiz buraxılmır. asyncio.Semaphore vasitəsilə eyni anda işləyən mənbə çağırışlarının yuxarı həddi müəyyən edilir. Sazlamada hədd üçdürsə, üç mənbə birlikdə işləyə bilər. Hədd birə endirilərsə eyni kod ardıcıl benchmark rejimi kimi işləyir. Semaphore provayderin dəqiq saniyəlik və ya günlük kvotasını hesablamır; yalnız eyni anda aktiv olan əməliyyatların sayını məhdudlaşdırır.

asyncio.gather bütün mənbə coroutine-lərini gözləyir. return_exceptions parametrinin doğru olması gözlənilməz bir mənbə xətasının digər nəticələri ləğv etməsinin qarşısını alır. Gather nəticələri mənbələrin bitmə sırası ilə deyil, ona verilən giriş sırası ilə qaytarır. Bu xüsusiyyət mənbə sırasının və sonrakı istinad nömrələrinin sabit saxlanmasına kömək edir.”

### Oxuyucu üçün prosesin izahı

Ardıcıl və paralel gözləməni sadə nümunə ilə müqayisə etmək olar. Üç mənbə 2, 4 və 3 saniyəyə cavab verirsə:

- Ardıcıl icrada təxminən 2 + 4 + 3 = 9 saniyə gözlənilir.
- Paralel icrada sorğular birlikdə başladıldığı üçün təxminən ən uzun müddət, yəni 4 saniyə gözlənilir.

Real nəticə tam olaraq 4 saniyə olmaya bilər. Sorğunun hazırlanması, event loop planlaşdırması, keş işi, bağlantı məhdudiyyətləri və server dəyişkənliyi əlavə vaxt yaradır. Buna görə sürətlənmə nəzəri mənbə sayına həmişə bərabər olmur.

Prosesin kod səviyyəsində axını:

1. Semaphore obyekti yaradılır.
2. Hər mənbə üçün bounded adlı coroutine hazırlanır.
3. Coroutine semaphore daxilinə girə bildikdə mənbə işi başlayır.
4. Gather bütün coroutine-ləri toplayır.
5. Hər nəticə seçilmiş mənbə sırası ilə uyğunlaşdırılır.
6. Xətalar SourceOutcome məlumatına çevrilir.

### Terminlərin izahı

- **Coroutine:** await nöqtəsində gözləməni dayandırıb idarəni event loop-a qaytara bilən asinxron funksiya.
- **Event loop:** Hansı coroutine-in hazır olduğunu izləyən və onları növbə ilə irəli aparan icra mexanizmi.
- **await:** Gözlənilən əməliyyat tamamlanana qədər həmin coroutine-i saxlayan, digər işlərə imkan verən ifadə.
- **Semaphore:** Eyni anda müəyyən saydan artıq işin kritik hissəyə daxil olmasını dayandıran sayğac.
- **asyncio.gather:** Bir neçə asinxron əməliyyatın tamamlanmasını birlikdə gözləyən funksiya.
- **return_exceptions:** Xətanın dərhal yuxarı atılması əvəzinə nəticələr sırasında obyekt kimi qaytarılması seçimi.
- **Rate limiter:** Müəyyən vaxt intervalında icazə verilən sorğu sayını idarə edən mexanizm; semaphore ilə eyni deyil.

## 8. Mənbə cavab vermədikdə

### Danışılacaq mətn

“Uzaq xidmətə göndərilən sorğunun nə vaxt cavab verəcəyi tətbiqin tam nəzarətində deyil. Şəbəkə kəsilə, provayder gecikə və ya bağlantı açıq qalıb heç bir nəticə qaytarılmaya bilər. Buna görə hər mənbə üçün ayrıca vaxt büdcəsi tətbiq edilir.

Slayddakı real kodda deadline kontekstinə per_source_timeout_seconds dəyəri verilir. Bu kontekst yalnız bir HTTP cəhdini deyil, həmin mənbə üçün fetch və retry əməliyyatlarının hamısını əhatə edir. Məsələn, büdcə on saniyədirsə, üç cəhdin hər biri üçün ayrıca on saniyə verilmir. Bütün cəhdlər birlikdə on saniyə daxilində tamamlanmalıdır.

Vaxt bitdikdə UpstreamTimeoutError yaradılır və AIService tərəfindən SourceOutcome obyektinə çevrilir. Bu obyekt mənbənin adını, TIMEOUT statusunu, təhlükəsiz xəta təsvirini, sərf olunan vaxtı və cəhd sayını saxlayır. Orchestrator digər mənbələrin nəticələrini itirmir. Slayddakı nümunədə arXiv vaxtı aşsa da Wikipedia və veb uğurlu qaldığı üçün onların materialı ilə qismən cavab yaradıla bilər.

Burada vaxt büdcəsinin sərhədi dəqiq göstərilməlidir. Mənbə fetch-i və retry həmin büdcəyə daxildir. Keş oxusu fetch-dən əvvəl, keş yazısı isə fetch-dən sonra aparıldığı üçün bu deadline onları əhatə etmir. PostgreSQL sorğularına command_timeout qoyulub, lakin connection pool-dan boş bağlantının alınması üçün ayrıca acquire timeout-u hazırkı kodda yoxdur. Buna görə ‘bütün mənbə işi mütləq on saniyəyə bitir’ demək düzgün deyil.”

### Oxuyucu üçün prosesin izahı

Timeout baş verdikdə proses belə davam edir:

1. Mənbə üçün vaxt sayğacı başlanır.
2. Fetch əməliyyatı icra edilir.
3. Retry edilə bilən tez xəta alınarsa qalan büdcə daxilində yenidən cəhd edilir.
4. Büdcə bitərsə cari coroutine ləğv siqnalı alır.
5. Timeout tətbiqin UpstreamTimeoutError kateqoriyasına çevrilir.
6. Xəta terminalda təhlükəsiz formada göstərilə bilən FailureDetail məlumatına salınır.
7. Digər mənbələrin nəticələri birləşdirilir.

Qismən nəticə ilə uğursuz nəticə fərqlidir. Ən azı bir istifadə edilə bilən mənbə və uğurlu sintez varsa PARTIAL statusu yarana bilər. Heç bir mənbə yoxdursa sintez ümumiyyətlə çağırılmır və NO_SOURCES statusu qaytarılır.

### Terminlərin izahı

- **Deadline:** Bütöv əməliyyatın bitməli olduğu son vaxt həddi.
- **Timeout:** Gözləmə müddətinin verilmiş həddi keçməsi.
- **Per-source timeout:** Hər mənbə üçün ayrıca tətbiq edilən vaxt büdcəsi.
- **Upstream:** Tətbiqin çağırdığı xarici xidmət.
- **SourceOutcome:** Bir mənbə çağırışının statusunu, nəticəsini və ya xətasını saxlayan model.
- **FailureDetail:** Canlı exception obyektini deyil, saxlanıla bilən xəta kodu, mesaj, mənbə və retry məlumatını daşıyan model.
- **Connection pool:** Təkrar istifadə üçün açıq saxlanılan verilənlər bazası bağlantıları dəsti.

## 9. HTTP 429 cavabının emalı

### Danışılacaq mətn

“HTTP 429 cavabı provayderin cari anda əlavə sorğu qəbul etmədiyini bildirir. Bu hal çox vaxt dəqiqəlik, günlük və ya modelə aid kvotanın dolması ilə əlaqəli olur. 18 sentyabr yoxlaması zamanı Gemini tərəfindən RESOURCE_EXHAUSTED cavabı qaytarılmış və təxminən 21,9 saniyə sonra yenidən cəhd edilməsi istənmişdi.

Adi retry siyasətində ilk xətadan sonra qısa exponential backoff və jitter hesablanır. Lakin provayder iyirmi bir saniyədən çox gözləmə tələb etdiyi halda yarım saniyə sonra yenidən cəhd edilməsi faydasızdır. Yeni cəhd eyni qapalı kvota pəncərəsinə düşür və yenə 429 alınır.

Hazırkı tərcümə qatında əvvəlcə provayder xəta mesajında ‘retry in’ və ya ‘Retry-After’ formasında gecikmə axtarılır. Mətn daxilində rəqəm tapılmadıqda exception chain boyunca HTTP response və onun Retry-After başlığı yoxlanılır. Yalnız gecikmə rəqəmi tətbiqin UpstreamRateLimitError obyektinə keçirilir; provayderin bütün xam mesajı istifadəçi çıxışına daşınmır.

Retry zamanı tətbiqin jitterli backoff müddəti ilə provayder tərəfindən tələb olunan gecikmə müqayisə edilir və daha böyük olan seçilir. Bu gözləmə max_backoff ilə kəsilmir, çünki provayderin öz kvotası barədə verdiyi göstəriş üstün sayılır. Bununla belə bütün sintez və ya mənbə əməliyyatının deadline-ı qüvvədə qalır. Deadline provayderin gözləmə müddətindən tez bitərsə əməliyyat timeout ilə tamamlanır.”

### Oxuyucu üçün prosesin izahı

Retry mexanizmi bütün xətalarda istifadə olunmur. Məsələn, səhv API açarı yenidən cəhd etməklə düzəlməyəcək. Şəbəkənin müvəqqəti kəsilməsi, 502 cavabı və 429 kimi hallar isə retry edilə bilər.

Exponential backoff ardıcıl cəhdlər arasında fasiləni böyüdür. Jitter həmin fasiləyə təsadüfi dəyişiklik əlavə edir. Bir neçə əməliyyat eyni vaxtda xəta alarsa, hamısının eyni anda yenidən provayderə qayıtmasının qarşısı bu yolla alınır.

Exception chain-in yoxlanması ona görə lazımdır ki, verilmiş AI paketi SDK xətasını ProviderError ilə əhatə edir. HTTP response birbaşa üst xətada deyil, onun cause hissəsində qala bilər. Zəncir izlənərək başlıq tapılır.

### Terminlərin izahı

- **HTTP 429:** Too Many Requests; sorğu tezliyi və ya kvota səbəbindən müvəqqəti rədd cavabı.
- **Kvota:** Müəyyən vaxt və ya hesab üçün icazə verilmiş istifadə həddi.
- **Retry:** Uğursuz əməliyyatın yenidən icra edilməsi.
- **Exponential backoff:** Hər növbəti retry-dən əvvəl gözləmə tavanının artırılması.
- **Jitter:** Eyni vaxtda retry dalğasının yaranmaması üçün fasiləyə təsadüfilik əlavə edilməsi.
- **Retry-After:** Növbəti sorğuya qədər nə qədər gözlənilməli olduğunu bildirən HTTP başlığı.
- **Exception chain:** Bir xətanın başqa xətadan yarandığını göstərən cause və context əlaqələri.

## 10. Təkrar sorğuların keşlə azaldılması

### Danışılacaq mətn

“Keşin məqsədi eyni mənbə axtarışının qısa müddət ərzində yenidən aparılmasını azaltmaqdır. Burada son AI cavabı keşlənmir. Hər mənbədən alınmış Source obyektləri saxlanılır. Buna görə keş hit baş verdikdə mənbə axtarışı buraxıla bilər, lakin cavab sintezi yenə icra edilir.

Keş açarı beş hissədən ibarətdir: mənbə adı, sualın kanonik forması, konkret provayder, hər mənbə üçün nəticə sayı və sxem versiyası. Bu sahələrdən hər hansı biri fərqli olduqda başqa keş qeydi seçilir. Məsələn, veb provayderi Tavily-dən DuckDuckGo-ya dəyişdirilərsə əvvəlki provayderin nəticəsinin qaytarılması istənilmir. Eyni qaydada üç nəticə üçün saxlanmış qeyd beş nəticə tələb edilən sorğunu tam qarşılamır.

Orchestrator əvvəlcə CacheService vasitəsilə açara uyğun təzə qeyd axtarır. Təzə qeyd tapıldıqda provider çağırışı edilmədən SourceOutcome qaytarılır; attempts dəyəri sıfır və cache_hit doğru olur. Qeyd tapılmadıqda və ya vaxtı bitdikdə canlı mənbə çağırılır. Uğurlu nəticə alındıqdan sonra yeni CacheEntry yaradılıb saxlanılır. Keş oxu və yazı xətaları əsas tədqiqat nəticəsini ləğv etmir; xəbərdarlıq əlavə edilir və mümkün olduqda canlı axtarış davam etdirilir.

Normallaşdırma ilə bağlı bilinən kənar hal qalır. Sualın sonundakı ulduz simvolu kanonik formadan silinir. Buna görə ‘what is C’ və ‘what is C*’ eyni açara çevrilə bilər. C və C sharp toqquşması düzəldilib, çünki hash işarəsi artıq silinmir. Lakin sxem versiyası bir olaraq qaldığı üçün əvvəlki yanlış C sharp qeydi müddəti bitənə qədər risk yarada bilər.”

### Oxuyucu üçün prosesin izahı

Cache-aside modeli tətbiq edilir:

1. Tətbiq əvvəlcə keşdən oxuyur.
2. Təzə qeyd varsa ondan istifadə edilir.
3. Qeyd yoxdursa canlı mənbə çağırılır.
4. Uğurlu nəticə keşə yazılır.
5. Növbəti eyni sorğu təzə qeyd bitməyibsə onu oxuyur.

TTL bitdikdən sonra köhnə qeyd qaytarılmır. PostgreSQL sorğusunda expires_at cari vaxtdan böyük olmalıdır. Ayrıca purge əmri vaxtı bitmiş qeydləri cədvəldən silə bilir.

Kanonikləşdirmə balans tələb edir. Çox zəif normallaşdırma eyni sual üçün lazımsız keş miss yaradır. Həddən artıq aqressiv normallaşdırma isə fərqli sualları eyni açara salaraq yanlış keş hit yarada bilər. Yanlış hit daha təhlükəlidir, çünki istifadəçiyə başqa sualın mənbələri səssizcə verilə bilər.

### Terminlərin izahı

- **Keş:** Daha əvvəl alınmış nəticənin sonrakı istifadə üçün müvəqqəti saxlandığı qat.
- **Cache hit:** Sorğuya uyğun təzə qeydin tapılması.
- **Cache miss:** Uyğun təzə qeydin tapılmaması.
- **TTL:** Time To Live; qeydin nə qədər müddət təzə sayılması.
- **Cache-aside:** Əvvəl keşə baxılan, nəticə yoxdursa əsas mənbədən alınıb keşə yazılan model.
- **Kanonikləşdirmə:** Sualın müqayisə üçün standart formaya gətirilməsi.
- **Sxem versiyası:** Keş payload formatı dəyişdikdə köhnə və yeni qeydləri ayırmaq üçün açara daxil edilən rəqəm.

## 11. AI xidməti ilə inteqrasiya

### Danışılacaq mətn

“Tətbiqin xarici AI paketi ilə əlaqəsi AIService qatında mərkəzləşdirilib. Məqsəd əsas istifadə halının Google, Anthropic və ya OpenAI SDK-sına aid sinif və xəta formalarını tanımamasıdır. Provayder seçimi sazlamadan oxunur, uyğun obyekt yaradılır və xarici ProviderError tətbiqin öz xəta kateqoriyalarına çevrilir.

Mənbə toplama üçün fetch_source metodu istifadə olunur. Wikipedia, arXiv və veb mənbəsi seçiminə uyğun fetch yolu çağırılır. Mənbə səviyyəsindəki timeout, boş nəticə və provayder xətası exception kimi bütün tətbiqi dayandırmır; SourceOutcome məlumatına çevrilir. Standart Wikipedia fulltext rejimi burada sənədləşdirilmiş istisnadır: təbii dildə uzun suallar üçün verilmiş opensearch yolunun nəticəsi zəif olduğuna görə MediaWiki fulltext API-si researcher xidmətindən birbaşa çağırılır.

Sintez mərhələsi verilmiş ai.synthesize funksiyasından istifadə edir. Həmin funksiya sinxron olduğu üçün birbaşa event loop daxilində çağırılsa digər asinxron işlər bloklana bilər. Buna görə asyncio.to_thread ilə işçi thread-ə verilir. Əsas coroutine thread-in nəticəsini await edir və bu vaxt event loop başqa hazır işləri işlədə bilir.

Bu seçimin məhdudiyyəti var. asyncio timeout coroutine-in gözləməsini dayandıra bilər, lakin Python-da artıq işləyən adi thread təhlükəsiz şəkildə məcburi dayandırılmır. Buna görə timeout istifadəçiyə vaxtında bildirilə bilər, amma provayder çağırışı arxa planda bir müddət davam edib prosesin tam bağlanmasını gecikdirə bilər. Bu hal mövcud məhdudiyyətlər slaydında ayrıca qeyd edilir.”

### Oxuyucu üçün prosesin izahı

AIService adapter rolunu oynayır. Xarici paketdən gələn anlayışlar tətbiqin daxili dilinə çevrilir:

- ProviderError → ConfigurationError, UpstreamRateLimitError və ya UpstreamError.
- Xarici source nəticəsi → tətbiqin SourceOutcome modeli.
- Sintez nəticəsi → yoxlanmış AnswerWithCitations.

Bu sərhəd sayəsində ResearchService yalnız “mənbə nəticəsi”, “cavab” və “tətbiq xətası” anlayışları ilə işləyir. Provayder dəyişdirildikdə əsas biznes axınının dəyişdirilməsi tələb olunmur.

Thread və coroutine fərqi dəqiq saxlanmalıdır. Coroutine event loop tərəfindən idarə olunur və await nöqtəsində dayana bilir. Thread əməliyyat sistemi səviyyəsində ayrıca icra xəttidir. to_thread sinxron funksiyanı coroutine kimi gözləməyə imkan verir, amma funksiyanı özü asinxron etmir.

### Terminlərin izahı

- **Adapter:** Xarici interfeysi tətbiqin gözlədiyi daxili formaya çevirən qat.
- **SDK:** Xarici xidmətlə işləmək üçün provayder tərəfindən verilən proqram kitabxanası.
- **Provider:** AI və ya axtarış xidmətini təqdim edən xarici sistem.
- **Sinxron funksiya:** Çağıran icra xəttini nəticə qayıdana qədər saxlayan funksiya.
- **Thread:** Proses daxilində ayrıca icra xətti.
- **asyncio.to_thread:** Sinxron funksiyanı işçi thread-də başladıb nəticəsini await etməyə imkan verən vasitə.
- **Event loop-un bloklanması:** Uzun sinxron iş səbəbindən digər coroutine-lərin vaxtında işləyə bilməməsi.

## 12. Məlumatların PostgreSQL-də saxlanması

### Danışılacaq mətn

“PostgreSQL-də iki ayrı saxlama məqsədi üçün iki cədvəl yaradılıb. source_cache cədvəli mənbə axtarışının müvəqqəti nəticələrini saxlayır. Onun əsas açarı source, query, provider, max_results və schema_version sütunlarından ibarətdir. Bu beşlik bir keş qeydini unikal müəyyən edir. Payload JSONB formatında saxlanılır; fetched_at məlumatın alındığı, expires_at isə təzə sayılmasının bitdiyi vaxtı göstərir.

research_sessions cədvəlində tamamlanmış tədqiqat icrası saxlanılır. id UUID tipində əsas açardır. created_at, status və question ayrıca sütun kimi verilir ki, son sessiyalar bütün JSON sənədləri açılmadan sıralana bilsin. payload sütununda isə ResearchSession modelinin tam JSON forması saxlanılır. Buraya istifadəçi sorğusu, provider məlumatı, bütün mənbə nəticələri, cavab, xəbərdarlıqlar və vaxtlar daxildir.

Saxlama qatının yuxarı hissəsində SourceCache, SessionRepository və Storage Protocol-ları var. Protocol konkret sinifdən miras almağı tələb etmir; tələb olunan metodların mövcud olmasını yoxlayır. PostgresSourceCache və PostgresSessionRepository həmin müqavilələrin real baza implementasiyasıdır. InMemorySourceCache və InMemorySessionRepository isə eyni metodları proses yaddaşında həyata keçirir.

Yazı əməliyyatlarında parametrli SQL istifadə olunur. Dəyərlər SQL mətninə birləşdirilmir, ayrıca parametrlər kimi driver-ə verilir. Təkrar açar üçün INSERT ON CONFLICT DO UPDATE işlədilir. Bununla eyni keş açarının və ya sessiya id-sinin təkrar yazılması xəta yaratmır; mövcud qeyd atomik şəkildə yenilənir.”

### Oxuyucu üçün prosesin izahı

Keş və sessiya eyni şey deyil:

- Keş performans üçündür və vaxtı bitə bilər.
- Sessiya tarixçə və audit üçündür; tamamlanmış icranın bütöv şəklini saxlayır.

JSONB istifadəsi məlumat modelinin bütün daxili hissələrini bir sənəd kimi saxlamağa imkan verir. Bununla yanaşı, tez-tez soruşulan sahələr ayrıca sütunlarda təkrarlanır. Bu denormalizasiya sayəsində məsələn, “ən son 20 sessiya” sorğusu hər payload-ı açmadan created_at indeksi ilə icra edilir.

Migration faylı cədvəlləri və indeksləri yaradır. Tətbiq başlananda tətbiq edilmiş migration-lar schema_migrations cədvəlində yoxlanılır. Eyni migration faylının sonradan dəyişdirilməsi checksum fərqi ilə rədd edilir.

### Terminlərin izahı

- **PostgreSQL:** Açıq mənbəli əlaqəli verilənlər bazası idarəetmə sistemi.
- **Cədvəl, sətir, sütun:** Verilənlər bazasında struktur, bir qeyd və həmin qeydin sahələri.
- **Primary key:** Sətri unikal müəyyən edən sütun və ya sütunlar dəsti.
- **UUID:** Qlobal miqyasda təkrarlanma ehtimalı çox aşağı olan identifikator.
- **JSONB:** PostgreSQL-də indekslənə və sorğulana bilən ikili JSON formatı.
- **Repository:** Məlumatın oxunması və yazılmasını domen kodundan ayıran obyekt.
- **Parametrli SQL:** Dəyərin SQL mətninə yapışdırılmadığı, ayrıca parametr kimi göndərildiyi sorğu.
- **Upsert:** Qeyd yoxdursa əlavə edən, varsa yeniləyən əməliyyat.
- **Migration:** Verilənlər bazası sxemində versiyalı dəyişiklik.

## 13. Tətbiqin işə salınması

### Danışılacaq mətn

“Tətbiq terminalda python -m researcher ask əmri ilə işə salınır. Əmr sətrindən sual, mənbə seçimi, keşin söndürülməsi və nəticə sayı kimi parametrlər qəbul edilir. Bu məlumat ResearchRequest modelinə çevrilir və əsas xidmətə ötürülür.

Xidmət çağırılmadan əvvəl bootstrap mərhələsi icra edilir. Burada mühit dəyişənləri Settings obyektinə oxunur və yoxlanılır. Logging sazlanır, ortaq HTTP client yaradılır, DATABASE_URL göstərilibsə PostgreSQL connection pool açılır və migration-lar tətbiq edilir. Daha sonra AIService, CacheService, Orchestrator və ResearchService obyektləri yaradılıb bir-birinə ötürülür. Bu yanaşma composition root adlanır, çünki tətbiqin obyekt qrafı bir yerdə qurulur.

Bootstrap asinxron context manager kimi işləyir. Bu seçim resursların yalnız uğurlu ssenaridə deyil, xəta və istifadəçi tərəfindən dayandırılma halında da bağlanmasını təmin edir. Tətbiq bitdikdə HTTP client və PostgreSQL pool aclose vasitəsilə sərbəst buraxılır. Yarımçıq açıq bağlantıların saxlanmasının qarşısı alınır.

Canlı cavab üçün internet bağlantısı və seçilmiş LLM provayderinə aid API açarı tələb olunur. Veb mənbəsi istifadə edilirsə uyğun axtarış provayderi üçün də açar lazım ola bilər. PostgreSQL məcburi deyil: DATABASE_URL verilmədikdə tətbiq bazasız rejimdə işləyir, keş və sessiya saxlaması buraxılır. DATABASE_URL mövcud olduğu halda PERSIST_SESSIONS false seçimi yalnız sessiya yazısını söndürür; mənbə keşi bazadan istifadə etməyə davam edə bilər.”

### Oxuyucu üçün prosesin izahı

İşə düşmə ardıcıllığı:

1. CLI arqumentləri parse edilir.
2. Settings yaradılır və mühit dəyişənləri yoxlanılır.
3. Logging handler-i qurulur.
4. HTTP bağlantı hovuzu yaradılır.
5. Baza ünvanı varsa PostgreSQL pool açılır və sxem yoxlanılır.
6. Provayder və xidmət obyektləri yaradılır.
7. ResearchService sorğunu icra edir.
8. Nəticə stdout və stderr-ə bölünərək göstərilir.
9. finally və ya context manager çıxışında resurslar bağlanır.

stdout yalnız cavab üçün istifadə edilir. Buna görə çıxış fayla yönləndiriləndə oxuna bilən cavab saxlanılır. Xəbərdarlıqlar və diaqnostika stderr-ə yazılır. Bu ayrılıq terminal alətlərinin bir-biri ilə işləməsini asanlaşdırır.

### Terminlərin izahı

- **Argument parser:** Terminalda yazılmış seçimləri və dəyərləri strukturlaşdıran komponent.
- **Environment variable:** Koddan kənarda saxlanan sazlama dəyəri.
- **Settings:** Mühit dəyişənlərini yoxlanmış tətbiq parametrlərinə çevirən obyekt.
- **Context manager:** Bloka giriş və çıxış zamanı resursların açılıb bağlanmasını idarə edən struktur.
- **HTTP client:** Uzaq HTTP xidmətlərinə sorğu göndərən və bağlantıları təkrar istifadə edən obyekt.
- **stdout:** Proqramın əsas standart çıxış kanalı.
- **stderr:** Xəta və diaqnostika üçün standart çıxış kanalı.
- **Bazasız rejim:** PostgreSQL qoşulmadan cavabın hazırlandığı, lakin daimi keş və sessiya saxlamasının olmadığı rejim.

## 14. Test nəticələri və onların sərhədi

### Danışılacaq mətn

“Layihənin cari test dəstində 380 test var. PostgreSQL əlçatan olduqda bütün testlər keçir. researcher paketində ölçülən sətir əhatəsi yuvarlaq olaraq 96 faizdir. Bundan əlavə, verilmiş ai paketinin ictimai müqaviləsini yoxlayan 16 smoke test dəyişdirilmədən keçir.

Testlərin böyük hissəsi offline işləyir. Xarici AI provayderi üçün saxta obyektlər, HTTP çağırışları üçün isə respx cavabları istifadə olunur. Bununla eyni xətanın və cavabın hər testdə təkrarlanması təmin edilir, kvota və şəbəkə dəyişkənliyi test nəticəsinə təsir etmir. conftest faylındakı avtomatik no_internet fixture-i public şəbəkə ünvanlarına bağlantını bloklayır. Loopback ünvanlarına icazə verilir ki, eyni maşındakı PostgreSQL inteqrasiya testləri işləyə bilsin.

Səkkiz test real PostgreSQL sxemi və repository müqaviləsini yoxlayır. Baza əlçatan olmadıqda bu testlər səssiz keçmiş sayılmır; səbəb göstərilməklə skip edilir. Baza qoşulduqda migration, cache contract, session contract, reconnect və dəyişdirilmiş migration-ın rədd edilməsi kimi davranışlar yoxlanılır.

Coverage rəqəmi düzgün şərh edilməlidir. 96 faiz kod sətirlərinin test zamanı icra edildiyini göstərir. Bu rəqəm test assertion-larının keyfiyyətini, bütün mümkün halların əhatə olunmasını və AI cavablarının 96 faiz doğru olmasını göstərmir. Real provayderin əlçatanlığı da offline testlə sübut edilmir. Buna görə test nəticəsi, təmiz mühit reproduksiyası və ayrıca canlı CLI icrası fərqli sübutlar kimi təqdim edilir.”

### Oxuyucu üçün prosesin izahı

Test növləri:

1. **Unit test:** Tək funksiya və ya sinif saxta asılılıqlarla yoxlanılır.
2. **Contract test:** İki implementasiyanın eyni qaydalara əməl etməsi yoxlanılır. Yaddaş və PostgreSQL storage eyni contract suite ilə sınaqdan keçirilir.
3. **Integration test:** Tətbiq kodu real PostgreSQL kimi xarici komponentlə birlikdə yoxlanılır.
4. **End-to-end daxili test:** CLI-dən xidmət və saxlamaya qədər bütöv tətbiq axını yoxlanılır; xarici AI saxta ola bilər.
5. **Live verification:** Həqiqi provayder və mənbələr ilə ayrıca icra aparılır.

Offline guard-un özü də test olunur. Yalnız guard əlavə edib ona inanmaq kifayət deyil; private və loopback ünvanlarının qəbul, public ünvanların rədd edilməsi ayrıca hallarla yoxlanılır.

### Terminlərin izahı

- **pytest:** Python testlərini tapıb icra edən test framework-u.
- **Fixture:** Testlərə hazırlıq vəziyyəti və asılılıq verən pytest mexanizmi.
- **Mock və fake:** Real xarici komponentin davranışını nəzarətli şəkildə əvəz edən test obyektləri.
- **respx:** httpx sorğularını test daxilində saxtalaşdıran kitabxana.
- **Integration test:** Bir neçə real komponentin birlikdə işləməsini yoxlayan test.
- **Smoke test:** Əsas ictimai funksiyaların pozulmadığını sürətli yoxlayan test.
- **Coverage:** Test zamanı icra edilmiş kod sətirlərinin payı.
- **Skip:** Şərt mövcud olmadıqda testin səbəbi göstərilərək icra edilməməsi.

## 15. Benchmark nəticələri

### Danışılacaq mətn

“Paralel mənbə toplamanın təsiri ayrıca benchmark ilə ölçülüb. Eyni beş tədqiqat sualı iki rejimdə icra edilib. Birinci rejimdə paralellik həddi bir seçilib və mənbələr faktiki ardıcıl toplanıb. İkinci rejimdə hədd üç seçilib və Wikipedia, arXiv və veb sorğuları birlikdə işlədilib. Hər iki rejimdə eyni suallar, eyni mənbələr və hər mənbə üçün eyni nəticə sayı istifadə olunub. Keş söndürülüb ki, ikinci rejim əvvəlki nəticəni oxumaqla süni üstünlük qazanmasın.

Beş sual üzrə ardıcıl mənbə toplamanın cəmi 4,83 saniyə, paralel toplamanın cəmi 2,89 saniyə olub. Orta sual üzrə vaxt 0,97 saniyədən 0,58 saniyəyə düşüb. Bu, retrieval mərhələsi üçün 1,67 dəfə sürətlənmədir. Ölçmədə hər iki rejim üzrə bütün mənbə çağırışları uğurlu olub.

Tam icra nəticəsi fərqlidir. Ardıcıl rejimdə retrieval və synthesis mərhələlərinin cəmi 43,45 saniyə, paralel rejimdə 43,33 saniyə olub. Yuvarlaq sürətlənmə 1,00 dəfədir. Səbəb sintez mərhələsinin 38–40 saniyə çəkməsi və ümumi vaxtın böyük hissəsini tutmasıdır. Mənbə toplamada qazanılan təxminən 1,94 saniyə bu daha böyük mərhələnin yanında ümumi nəticəni az dəyişib.

Benchmark 12 sentyabrda gemini-3.6-flash modeli ilə aparılıb. Pulsuz kvotanın qorunması üçün suallar arasında on saniyə fasilə verilib. Həmin fasilə hər sualın retrieval, synthesis və end-to-end mərhələ vaxtlarına daxil deyil; yalnız tam sweep wall-clock vaxtına təsir edir. 18 sentyabr canlı icrası başqa model və tək sual ilə aparılıb, buna görə onun 4,97 saniyəlik nəticəsi bu benchmark cədvəlinə əlavə edilmir.”

### Oxuyucu üçün prosesin izahı

Ədalətli müqayisə üçün dəyişənlər nəzarətdə saxlanmalıdır:

- Sual dəsti eyni olmalıdır.
- Mənbələr eyni olmalıdır.
- Nəticə sayı eyni olmalıdır.
- Keş hər iki rejimdə eyni vəziyyətdə olmalıdır.
- Uğursuz sintezlə uğurlu sintez müqayisə edilməməlidir.

İlk benchmark cəhdlərindən birində provayder 429 qaytardığı üçün bəzi sintezlər dərhal rədd edilmişdi. Bu halda nəticə süni şəkildə sürətli görünmüşdü. Benchmark belə natamam sweep üçün end-to-end speedup göstərməyəcək şəkildə düzəldilib. Yalnız bütün müqayisə şərtləri ödənən run yekun artefakt kimi saxlanılıb.

### Terminlərin izahı

- **Benchmark:** İki yanaşmanın nəzarət olunan eyni şərtlərdə ölçülməsi.
- **Sequential:** İşlərin bir-birinin ardınca icra edilməsi.
- **Concurrent:** Gözləmə müddətləri üst-üstə düşəcək şəkildə bir neçə işin irəli aparılması.
- **Retrieval:** Xarici mənbələrdən materialın tapılıb gətirilməsi mərhələsi.
- **End-to-end mərhələ vaxtı:** Retrieval və synthesis vaxtlarının cəmi.
- **Sweep:** Bütün sual dəstinin bir rejimdə tam icrası.
- **Wall-clock:** İstifadəçinin saatla müşahidə etdiyi ümumi keçən vaxt.
- **Bottleneck:** Ümumi performansı ən çox məhdudlaşdıran mərhələ.

## 16. Təmiz mühitdə reproduksiya

### Danışılacaq mətn

“Layihənin yalnız müəllifin maşınında işləməsi kifayət etmir. Asılılıqların, Docker faylının, əmrlərin və sənədləşmənin başqa mühitdə eyni nəticəyə gətirib-gətirmədiyi də yoxlanmalıdır. Bu məqsədlə GitHub Codespaces daxilində təmiz klon yaradılıb və reproduksiya addımları ayrıca artefaktda qeydə alınıb.

Yoxlama zamanı repository yeni mühitə klonlanıb, Docker image qurulub və verilmiş ai paketinin 16 smoke testi container daxilində icra edilib. Daha sonra o vaxtkı tam test dəsti işə salınıb. Həmin commitdə 349 test keçib, yeddi PostgreSQL testi isə Codespaces mühitində containerlərarası bağlantı alınmadığı üçün skip edilib. Bu rəqəmlər cari 380 testlik vəziyyətə aid deyil; artefaktın yaradıldığı daha əvvəlki commitə aiddir.

Reproduksiya zamanı əhəmiyyətli əməliyyat problemi aşkarlanıb. Əlçatan olmayan PostgreSQL ünvanına qoşulma cəhdi driver-in standart davranışına görə təxminən 60 saniyə gözləyirdi. Bir neçə test eyni bağlantını yoxladıqda ümumi gözləmə xeyli uzanırdı. Bu tapıntı fatimekazimli tərəfindən artefaktda qeyd edilib, Elmin995 review şərhində follow-up düzəliş tələb edib və ii1ahe tərəfindən bağlantı açılmasına on saniyəlik timeout əlavə olunub.

Bu on saniyəlik hədd yalnız pool-un ilk bağlantısının açılmasına aiddir. Ayrı SQL əmrlərinə başqa command_timeout tətbiq edilir. Pool artıq dolu olduqda boş bağlantının acquire edilməsi üçün ayrıca hədd isə mövcud deyil. Həmçinin Codespaces reproduksiyasında canlı AI açarı istifadə edilməyib; buna görə həmin artefakt real cavabın uğurlu olduğunu deyil, qurulma, test və baza bağlantısı davranışını sübut edir.”

### Oxuyucu üçün prosesin izahı

Reproduksiya addımlarının məqsədi:

1. Gizli yerli fayldan asılılıq olub-olmadığını tapmaq.
2. requirements fayllarının tam olub-olmadığını yoxlamaq.
3. Docker image-in sıfırdan qurulduğunu göstərmək.
4. Test əmrlərinin sənəddə yazıldığı kimi işlədiyini yoxlamaq.
5. Başqa şəbəkə və container mühitində üzə çıxan fərqi qeyd etmək.

Təmiz mühitdə uğursuzluq həmişə tətbiq kodunun səhvi deyil. Codespaces-də containerlərarası şəbəkə fərqli ola bilər. Lakin tətbiqin bu uğursuzluğa necə cavab verdiyi yenə vacibdir. Bir dəqiqə səssiz gözləmə istifadəçi təcrübəsini və test müddətini pisləşdirdiyi üçün fail-fast bağlantı timeout-u əlavə olunub.

### Terminlərin izahı

- **Reproduksiya:** Eyni addımların başqa mühitdə təkrar edilərək nəticənin yoxlanması.
- **Təmiz klon:** Yerli, izlənməyən fayllar olmadan repository-nin yeni nüsxəsi.
- **GitHub Codespaces:** GitHub tərəfindən təqdim edilən uzaq development container mühiti.
- **Docker image:** Tətbiq və asılılıqlarını işə salmaq üçün dəyişməz paket şablonu.
- **Container:** Image-dən yaradılan izolyasiya olunmuş işlək proses mühiti.
- **Artefakt:** Yoxlamanın tarixini, əmrlərini və nəticəsini saxlayan sübut faylı.
- **Fail fast:** İstifadə edilə bilməyən vəziyyəti uzun gözləmə əvəzinə tez bildirmək.
- **Connection timeout:** Bağlantının açılması üçün maksimum gözləmə müddəti.

## 17. Canlı icranın nəticəsi

### Danışılacaq mətn

“Cari versiyanın xarici xidmətlərlə işləməsi 18 sentyabrda ayrıca canlı CLI icrası ilə yoxlanılıb. ‘What is photosynthesis and what are its main stages?’ sualı Wikipedia, arXiv və veb mənbələrinə göndərilib. Üç mənbədən gələn nəticələr birləşdirildikdən və təkrar URL-lər çıxarıldıqdan sonra səkkiz unikal mənbə saxlanılıb.

Həmin mənbə siyahısı gemini-3.1-flash-lite modelinə verilib. Model tərəfindən istinadlı cavab hazırlanıb. Cavabda [1], [7] və [8] markerləri qeydə alınıb və bu markerlərin bağlı olduğu mənbələr son siyahı ilə struktur baxımından uyğun olub. Tam ResearchSession PostgreSQL-də saxlanılıb. Beləliklə, yoxlama yalnız mənbə çağırışını deyil, girişdən saxlama mərhələsinə qədər real tətbiq yolunu əhatə edib.

Mənbə toplama mərhələsi 1,51 saniyə, sintez mərhələsi 3,46 saniyə çəkib. Tətbiqin TimingInfo modelində göstərilən iki əsas mərhələnin cəmi 4,97 saniyə olub. Bu cəm bootstrap, migration və terminal renderinin bütün xarici vaxtını əhatə edən tam proses wall-clock ölçüsü deyil; tətbiq tərəfindən ayrıca ölçülən retrieval və synthesis vaxtlarının cəmidir.

Həmin gün yeni standart modellə üç tam CLI icrası uğurlu tamamlanıb. Ondan əvvəl istifadə olunan gemini-3.8-flash mənbə materialı verilmiş sorğularda 504 və sintez timeout-u ilə qarşılaşıb. Buna görə standart model daha yüngül gemini-3.1-flash-lite ilə dəyişdirilib. Bu müşahidə həmin tarix və hesab üçün keçərlidir; xarici provayderin gələcəkdə eyni gecikmə və əlçatanlığı saxlayacağına zəmanət verilmir.”

### Oxuyucu üçün prosesin izahı

Canlı yoxlama ilə test dəsti arasındakı fərq:

- Offline testdə AI və HTTP cavabları əvvəlcədən idarə olunur.
- Canlı yoxlamada real internet, real axtarış xidmətləri və real LLM istifadə olunur.
- Offline test davranışın deterministik sübutudur.
- Canlı yoxlama xarici inteqrasiyanın həmin anda işlədiyini göstərən müşahidədir.

Səkkiz mənbənin hamısı cavabda istifadə edilməyib. Model yalnız [1], [7] və [8]-ə istinad edib. Digər mənbələr kontekst kimi verilə bilər, lakin citation obyektində göstərilmədiyi üçün istifadəçiyə istinad siyahısında yalnız faktiki citation-lar çıxarılır.

### Terminlərin izahı

- **Canlı icra:** Saxta cavablar əvəzinə real xarici xidmətlərdən istifadə edilən run.
- **Unikal mənbə:** Normallaşdırılmış URL-i başqa nəticə ilə təkrarlanmayan mənbə.
- **LLM:** Large Language Model; verilmiş mənbələrdən cavab hazırlayan dil modeli.
- **Citation marker:** Cavab mətnində [1] formasında görünən istinad işarəsi.
- **TimingInfo:** Retrieval, synthesis və ümumi mərhələ vaxtlarını saxlayan model.
- **HTTP 504:** Gateway Timeout; aradakı xidmətin yuxarı serverdən vaxtında cavab almadığını bildirən status.
- **Model default-u:** LLM_MODEL ayrıca verilmədikdə tətbiqin seçdiyi model identifikatoru.

## 18. Mövcud məhdudiyyətlər

### Danışılacaq mətn

“Sistemin işlək olması bütün texniki risklərin aradan qaldırıldığı mənasına gəlmir. Birinci məhdudiyyət cavabın faktiki dəstəyidir. Mövcud validation cavabın boş olmamasını və istinad nömrələrinin mənbə siyahısı ilə uyğunluğunu yoxlayır. Lakin cavabdakı hər iddianın göstərilən mənbədə həqiqətən mövcud olmasını və düzgün şərh edilməsini yoxlamır. Düzgün nömrəli istinadla yanlış və ya həddən artıq ümumiləşdirilmiş cümlə təqdim edilə bilər.

İkinci məhdudiyyət keş açarının kanonikləşdirilməsidir. Hash simvolunun silinməsi dayandırıldığı üçün C və C sharp toqquşması düzəldilib. Lakin ulduz simvolu hələ kənar simvollar siyahısına daxildir. Buna görə C və C star haqqında suallar eyni kanonik açara çevrilə bilər. Cache schema_version bir olaraq qaldığı üçün əvvəlki yanlış açarla yazılmış qeyd TTL bitənə qədər mövcud qala bilər.

Üçüncü məhdudiyyət baza vaxt sərhədidir. asyncpg üçün command_timeout təyin edilib və uzun SQL əmri xəta ilə dayandırıla bilir. Lakin repository metodları pool.acquire çağırışını ayrıca timeout daxilinə salmır. Pool-dakı bütün bağlantılar başqa işlərlə tutulubsa boş bağlantının gözlənilməsi mənbə deadline-ından uzun davam edə bilər.

Dördüncü məhdudiyyət sintez thread-inin ləğvidir. asyncio.to_thread event loop-un bloklanmasının qarşısını alır, lakin timeout baş verdikdə çalışan thread məcburi dayandırılmır. İstifadəçiyə timeout qaytarıldıqdan sonra xarici model çağırışı qısa müddət davam edə və provayder istifadəsi yarada bilər.

Bu məhdudiyyətlərin göstərilməsi tətbiqin istifadəsiz olması demək deyil. Onlar mövcud zəmanətin sərhədini müəyyən edir. Növbəti texniki addımlar iddia-mənbə uyğunluğu yoxlaması, daha təhlükəsiz kanonikləşdirmə və yeni keş versiyası, pool acquire timeout-u və ləğv edilə bilən və ya proses səviyyəsində izolyasiya olunan sintez ola bilər.”

### Oxuyucu üçün prosesin izahı

Hər məhdudiyyət üçün “hazırda nə qorunur?” və “nə qorunmur?” ayrılmalıdır:

- İstinadın mövqeyi qorunur; iddianın fakt dəstəyi qorunmur.
- C sharp açarı qorunur; son ulduz simvolu qorunmur.
- SQL əmri vaxtla məhdudlaşır; pool növbəsi ayrıca məhdudlaşmır.
- Coroutine deadline ilə dayandırılır; onun işlətdiyi sinxron thread dərhal dayandırılmır.

Bu fərqlər müdafiədə vacibdir. Daha geniş zəmanət verilməsi müəllimin konkret kodu açıb əks nümunə göstərməsinə səbəb ola bilər. Mövcud davranış olduğu kimi izah edilməlidir.

### Terminlərin izahı

- **Məhdudiyyət:** Sistemin hazırkı versiyada vermədiyi zəmanət və ya əhatə etmədiyi hal.
- **İddia-mənbə uyğunluğu:** Cavabdakı konkret cümlənin istinad edilən material tərəfindən dəstəklənməsi.
- **Cache-key collision:** İki fərqli sorğunun eyni keş açarına çevrilməsi.
- **Schema version:** Keş məlumat formatının versiya rəqəmi.
- **Pool acquisition:** Connection pool-dan boş baza bağlantısının alınması.
- **Cancellation:** Davam edən asinxron əməliyyatın dayandırılması tələbi.
- **İzolyasiya:** Riskli işi əsas prosesdən ayrı mühitdə icra etməklə təsirini məhdudlaşdırmaq.

## 19. Töhfələr və AI alətlərindən istifadə

### Danışılacaq mətn — ii1ahe

“Repository-dəki tətbiq kodunun, testlərin, Docker və PostgreSQL infrastrukturunun, benchmarkın, hesabatın və əsas sənədlərin böyük hissəsi ii1ahe tərəfindən hazırlanıb. Git tarixçəsində final tag üzrə tətbiq commitlərinin əsas hissəsi də həmin müəllifə aiddir. Təqdimat zamanı arxitektura, məlumat modelləri, validation, CLI, canlı icra və qalan məhdudiyyətlər bu rol əsasında izah edilir.

Kod və ilkin test qaralamalarının hazırlanmasında Claude-dan istifadə olunub. Hazırlanan material qəbul edilməzdən əvvəl testlərlə və canlı icralarla yoxlanılıb; benchmark zamanı tapılan sintez timeout-u, təsadüfən internetə çıxan testlər və provayder retry davranışı kimi problemlər sonradan dəyişdirilib. OpenAI Codex-dən yekun audit, sənəd uyğunsuzluqlarının düzəldilməsi, canlı model probleminin diaqnostikası, təqdimatın yenidən qurulması və danışıq materialının hazırlanmasında istifadə olunub. Bu istifadə töhfə sənədində ayrıca açıqlanıb.”

### Danışılacaq mətn — Elmin995

“Elmin995 tərəfindən PR 11 və PR 12 üzrə GitHub review aparılıb. Retry-After düzəlişində throttling marker kənar halı barədə inline qeyd verilib. Təmiz klon reproduksiyası PR-ında baza bağlantısı probleminin ayrıca follow-up düzəlişə çevrilməsi tələb edilib. Bu təqdimatda asinxron icra, timeout, retry, keş və AI sərhədi izah edilir. Həmin modulların kod müəllifliyi iddia edilmir; təqdimat rolu ilə faktiki repository töhfəsi ayrı saxlanılır.”

### Danışılacaq mətn — fatimekazimli

“fatimekazimli tərəfindən repository təmiz GitHub Codespaces mühitində yoxlanılıb və nəticələr reproduction artefaktında saxlanılıb. Docker image-in qurulması, verilmiş smoke testlərin icrası, o vaxtkı tam test dəsti və PostgreSQL bağlantı fərqi sənədləşdirilib. Əlçatan olmayan bazanın uzun müddət gözlənilməsi həmin yoxlamada aşkar edilib və sonrakı timeout düzəlişinə səbəb olub. Bu təqdimatda storage, test, benchmark və reproduksiya hissələri izah edilir. PostgreSQL modullarının müəllifliyi iddia edilmir.”

### Oxuyucu üçün prosesin izahı

Bu slayd üç ayrı anlayışı ayırır:

1. **Kod müəllifliyi:** Fayl və commitləri faktiki hazırlayan şəxs.
2. **Review və reproduksiya töhfəsi:** Başqasının kodunu yoxlamaq, problem tapmaq və sübut artefaktı yaratmaq.
3. **Təqdimat bölgüsü:** Müdafiə zamanı müəyyən texniki hissəni öyrənib izah etmək.

Bu üç anlayış eyni deyil. Bir modulu təqdim edən şəxsin onu yazdığı nəticəsi çıxarılmamalıdır. Eyni zamanda review və reproduksiya da real proqram mühəndisliyi işidir və konkret sübutla göstərilir.

AI istifadəsinin açıqlanması da “bütün işi AI edib” cümləsi ilə məhdudlaşmır. Hansı alətin hansı fayllarda qaralama hazırladığı, insan tərəfindən nəyin yoxlandığı, hansı AI nəticəsinin səhv olduğu və necə düzəldildiyi göstərilir. Komanda müdafiədə kodun davranışını izah etməyə cavabdeh qalır.

### Terminlərin izahı

- **Commit author:** Git commitində dəyişiklik müəllifi kimi göstərilən şəxs.
- **Pull request:** Branch dəyişikliklərinin main-ə birləşdirilməzdən əvvəl baxışa təqdim edilməsi.
- **Code review:** Dəyişikliyin düzgünlük, oxunaqlılıq və risk baxımından başqa şəxs tərəfindən yoxlanması.
- **Inline comment:** Pull request-də konkret kod sətrinə yazılan review qeydi.
- **Reproduksiya töhfəsi:** Sistemin başqa mühitdə qurulub yoxlanması və nəticənin sübut kimi saxlanması.
- **AI disclosure:** Süni intellekt alətlərinin harada və necə istifadə edildiyinin açıq göstərilməsi.
- **Faktiki rol:** Git tarixçəsi, review qeydləri və artefaktlarla təsdiqlənən iş.

## Məşq qaydası

Hər təqdimatçı əvvəlcə öz slaydının “Danışılacaq mətn” hissəsini oxumalı, sonra sənədi bağlayıb prosesi öz sözləri ilə izah etməlidir. Daha sonra “Prosesin izahı” hissəsindəki addımlar kodda tapılmalıdır. Son mərhələdə komanda yoldaşı terminlərdən birini seçib aşağıdakı üç sualı verməlidir:

1. Bu termin ümumi olaraq nə deməkdir?
2. Bu layihədə hansı fayl və ya funksiya ilə həyata keçirilir?
3. Onun hazırkı məhdudiyyəti nədir?

Əsas fakt mənbələri:

- **researcher/** — tətbiq kodu.
- **docs/architecture.md** — qatlar, qərarlar və ADR-lər.
- **artefacts/bench-report.txt** — 12 sentyabr benchmarkının tam nəticəsi.
- **artefacts/reproduction-codespaces-bfec78.txt** — təmiz mühit yoxlaması.
- **CONTRIBUTION_STATEMENT.md** — faktiki töhfələr və AI istifadəsi.
- **presentation/** — hər üzv üçün daha geniş texniki öyrənmə PDF-ləri.
