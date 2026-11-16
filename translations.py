"""
BarberBook translations: English (default), Russian (ru), Kazakh (kk).

No extra libraries. The English text itself is the key:

    t("Log out")                                  -> "Выйти" (ru)
    t("Barber {name} added successfully!", name=n) -> placeholders filled by str.format
    tn("{n} rating", 5)                           -> "5 оценок" (ru), plural form by number

How to add a new string:
  1. Write it in English in the code: t("New text") in app.py,
     {{ _("New text") }} in a template (`t` works in templates too).
  2. Add  "New text": {"ru": "...", "kk": "..."}  to TRANSLATIONS below.
     Keep {placeholders} exactly as in the English text.
  3. Strings that depend on a number go to PLURALS (English: 2 forms,
     Russian: 3 forms - 1 / 2-4 / 5+, Kazakh: 1 form) and are used with tn().
A string without a translation is shown in English (nothing breaks).
"""

from flask import g, has_request_context, request, session

# code -> (label in the switcher, language name)
LANGUAGES = {
    "en": ("EN", "English"),
    "ru": ("RU", "Русский"),
    "kk": ("KZ", "Қазақша"),
}
DEFAULT_LANG = "en"
LANG_COOKIE = "lang"
LANG_COOKIE_MAX_AGE = 365 * 24 * 60 * 60   # one year


def detect_lang():
    """session['lang'] -> cookie -> Accept-Language (ru/kk) -> en."""
    for value in (session.get("lang"), request.cookies.get(LANG_COOKIE)):
        if value in LANGUAGES:
            return value
    best = request.accept_languages.best_match(list(LANGUAGES))
    return best if best in LANGUAGES else DEFAULT_LANG


def get_lang():
    """Current language code; English outside a request."""
    if not has_request_context():
        return DEFAULT_LANG
    if "lang" not in g:
        g.lang = detect_lang()
    return g.lang


def t(text, **kwargs):
    """Translate `text` into the current language and fill {placeholders}."""
    lang = get_lang()
    if lang != DEFAULT_LANG:
        text = TRANSLATIONS.get(text, {}).get(lang, text)
    return text.format(**kwargs) if kwargs else text


def plural_index(lang, n):
    if lang == "ru":
        if n % 10 == 1 and n % 100 != 11:
            return 0
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return 1
        return 2
    if lang == "kk":
        return 0                      # Kazakh: the noun does not change after a number
    return 0 if n == 1 else 1         # English


def tn(key, n, **kwargs):
    """Plural-aware translation. `key` is the English singular from PLURALS."""
    lang = get_lang()
    forms = PLURALS.get(key, {})
    choice = forms.get(lang) or forms.get(DEFAULT_LANG) or (key,)
    text = choice[min(plural_index(lang, n), len(choice) - 1)]
    return text.format(n=n, **kwargs)


def money(amount):
    """4000.0 -> '4000 KZT' / '4000 ₸'"""
    return t("{price} KZT", price="%.0f" % (amount or 0))


def minutes(value):
    """45 -> '45 min' / '45 мин'"""
    return t("{minutes} min", minutes=value)


# ------------------------------------------------------------------
# Strings that depend on a number
# ------------------------------------------------------------------
PLURALS = {
    "{n} rating": {
        "en": ("{n} rating", "{n} ratings"),
        "ru": ("{n} оценка", "{n} оценки", "{n} оценок"),
        "kk": ("{n} баға",),
    },
    "{n} star": {
        "en": ("{n} star", "{n} stars"),
        "ru": ("{n} звезда", "{n} звезды", "{n} звёзд"),
        "kk": ("{n} жұлдыз",),
    },
    "{n} completed service": {
        "en": ("{n} completed service", "{n} completed services"),
        "ru": ("{n} выполненная услуга", "{n} выполненные услуги", "{n} выполненных услуг"),
        "kk": ("{n} орындалған қызмет",),
    },
    "{n} working hour blocked": {
        "en": ("{n} working hour blocked", "{n} working hours blocked"),
        "ru": ("заблокирован {n} рабочий час", "заблокировано {n} рабочих часа",
               "заблокировано {n} рабочих часов"),
        "kk": ("{n} жұмыс сағаты бұғатталды",),
    },
    "{n} hour was NOT blocked": {
        "en": ("{n} hour was NOT blocked because it already has bookings ({list}). "
               "Cancel those bookings first, then add the time off again.",
               "{n} hours were NOT blocked because they already have bookings ({list}). "
               "Cancel those bookings first, then add the time off again."),
        "ru": ("{n} час НЕ заблокирован: на него уже есть записи ({list}). "
               "Сначала отмените эти записи, затем добавьте перерыв снова.",
               "{n} часа НЕ заблокированы: на них уже есть записи ({list}). "
               "Сначала отмените эти записи, затем добавьте перерыв снова.",
               "{n} часов НЕ заблокировано: на них уже есть записи ({list}). "
               "Сначала отмените эти записи, затем добавьте перерыв снова."),
        "kk": ("{n} сағат бұғатталмады, себебі оларға жазылулар бар ({list}). "
               "Алдымен сол жазылулардан бас тартыңыз, содан кейін демалысты қайта қосыңыз.",),
    },
}


# ------------------------------------------------------------------
# All other strings: English text -> {"ru": ..., "kk": ...}
# ------------------------------------------------------------------
TRANSLATIONS = {
    # ---------- Common / header ----------
    "Language": {"ru": "Язык", "kk": "Тіл"},
    "Switch to dark theme": {"ru": "Включить тёмную тему", "kk": "Қараңғы тақырыпқа ауысу"},
    "Switch to light theme": {"ru": "Включить светлую тему", "kk": "Жарық тақырыпқа ауысу"},
    "BarberBook logo": {"ru": "Логотип BarberBook", "kk": "BarberBook логотипі"},
    "Log out": {"ru": "Выйти", "kk": "Шығу"},
    "Log in": {"ru": "Войти", "kk": "Кіру"},
    "Change password": {"ru": "Сменить пароль", "kk": "Құпиясөзді өзгерту"},
    "Account": {"ru": "Аккаунт", "kk": "Тіркелгі"},
    "Current password": {"ru": "Текущий пароль", "kk": "Ағымдағы құпиясөз"},
    "New password": {"ru": "Новый пароль", "kk": "Жаңа құпиясөз"},
    "Repeat new password": {"ru": "Повторите новый пароль", "kk": "Жаңа құпиясөзді қайталаңыз"},
    "Save new password": {"ru": "Сохранить новый пароль", "kk": "Жаңа құпиясөзді сақтау"},
    "Password": {"ru": "Пароль", "kk": "Құпиясөз"},
    "Phone number": {"ru": "Номер телефона", "kk": "Телефон нөмірі"},
    "First name": {"ru": "Имя", "kk": "Аты"},
    "Last name": {"ru": "Фамилия", "kk": "Тегі"},
    "Barber": {"ru": "Барбер", "kk": "Барбер"},
    "Barber: {name}": {"ru": "Барбер: {name}", "kk": "Барбер: {name}"},
    "Phone: {phone}": {"ru": "Телефон: {phone}", "kk": "Телефон: {phone}"},
    "Select barber": {"ru": "Выберите барбера", "kk": "Барберді таңдаңыз"},
    "Date": {"ru": "Дата", "kk": "Күні"},
    "Time": {"ru": "Время", "kk": "Уақыты"},
    "Date: {date}": {"ru": "Дата: {date}", "kk": "Күні: {date}"},
    "no ratings yet": {"ru": "пока нет оценок", "kk": "әзірге баға жоқ"},
    "No ratings yet": {"ru": "Пока нет оценок", "kk": "Әзірге баға жоқ"},
    "Today": {"ru": "Сегодня", "kk": "Бүгін"},
    "7 days": {"ru": "7 дней", "kk": "7 күн"},
    "30 days": {"ru": "30 дней", "kk": "30 күн"},
    "Revenue from completed services": {"ru": "Выручка за выполненные услуги",
                                        "kk": "Орындалған қызметтерден түскен табыс"},
    "No upcoming appointments.": {"ru": "Предстоящих записей нет.", "kk": "Алдағы жазылулар жоқ."},
    "Complete": {"ru": "Завершить", "kk": "Аяқтау"},
    "Cancel": {"ru": "Отменить", "kk": "Бас тарту"},
    "Break": {"ru": "Перерыв", "kk": "Үзіліс"},
    "{price} KZT": {"ru": "{price} ₸", "kk": "{price} ₸"},
    "{minutes} min": {"ru": "{minutes} мин", "kk": "{minutes} мин"},

    # ---------- Appointment statuses ----------
    "Booked": {"ru": "Запланирована", "kk": "Жазылған"},
    "Completed": {"ru": "Выполнена", "kk": "Аяқталды"},
    "Cancelled": {"ru": "Отменена", "kk": "Бас тартылды"},
    "Missed": {"ru": "Пропущена", "kk": "Келмеді"},

    # ---------- Services (display only, DB keeps English) ----------
    "Classic Haircut": {"ru": "Классическая стрижка", "kk": "Классикалық шаш қию"},
    "Fade Haircut": {"ru": "Стрижка фейд", "kk": "Фейд шаш қию"},
    "Beard Trim": {"ru": "Оформление бороды", "kk": "Сақал түзету"},
    "Haircut and Beard": {"ru": "Стрижка и борода", "kk": "Шаш қию және сақал"},
    "Buzz Cut": {"ru": "Стрижка под машинку", "kk": "Машинкамен шаш қию"},
    "Hair Styling": {"ru": "Укладка волос", "kk": "Шаш сәндеу"},
    "Textured Crop": {"ru": "Текстурный кроп", "kk": "Текстуралы кроп"},
    "Pompadour": {"ru": "Помпадур", "kk": "Помпадур"},
    "Kids Haircut": {"ru": "Детская стрижка", "kk": "Балалар шаш қию"},
    "Hot Towel Shave": {"ru": "Бритьё с горячим полотенцем", "kk": "Ыстық сүлгімен қырыну"},

    # ---------- Page titles ----------
    "Book your appointment": {"ru": "Запись на стрижку", "kk": "Шаш қиюға жазылу"},
    "Log in or register": {"ru": "Вход или регистрация", "kk": "Кіру немесе тіркелу"},
    "Admin Login": {"ru": "Вход для администратора", "kk": "Әкімші ретінде кіру"},
    "My Schedule": {"ru": "Моё расписание", "kk": "Менің кестем"},
    "Schedule Dashboard": {"ru": "Панель расписания", "kk": "Кесте тақтасы"},

    # ---------- Customer page (index.html) ----------
    "Book an appointment": {"ru": "Записаться", "kk": "Жазылу"},
    "Professional barber cutting a client's hair in a modern shop": {
        "ru": "Барбер стрижёт клиента в современном барбершопе",
        "kk": "Заманауи барбершопта барбер клиенттің шашын қиып жатыр"},
    "Your style. Your time.": {"ru": "Ваш стиль. Ваше время.", "kk": "Сіздің стиліңіз. Сіздің уақытыңыз."},
    "A better haircut": {"ru": "Хорошая стрижка", "kk": "Жақсы шаш үлгісі"},
    "starts with a booking.": {"ru": "начинается с записи.", "kk": "жазылудан басталады."},
    "Choose your barber, pick a time, and leave the rest to us.": {
        "ru": "Выберите барбера и время — остальное мы возьмём на себя.",
        "kk": "Барбер мен уақытты таңдаңыз — қалғанын бізге қалдырыңыз."},
    "Book now": {"ru": "Записаться", "kk": "Қазір жазылу"},
    "Play Music": {"ru": "Включить музыку", "kk": "Музыканы қосу"},
    "Pause Music": {"ru": "Пауза", "kk": "Музыканы тоқтату"},
    "YOUR NEXT LOOK": {"ru": "ВАШ НОВЫЙ ОБРАЗ", "kk": "СІЗДІҢ ЖАҢА БЕЙНЕҢІЗ"},
    "Look sharp.": {"ru": "Выглядите стильно.", "kk": "Сәнді көрініңіз."},
    "Feel confident.": {"ru": "Чувствуйте себя уверенно.", "kk": "Өзіңізге сенімді болыңыз."},
    "Simple booking. Great service.": {"ru": "Простая запись. Отличный сервис.",
                                       "kk": "Оңай жазылу. Керемет қызмет."},
    "Reserve your time": {"ru": "Забронируйте время", "kk": "Уақытыңызды брондаңыз"},
    "Fill in the details below to reserve your visit.": {
        "ru": "Заполните форму ниже, чтобы записаться на визит.",
        "kk": "Келуге жазылу үшін төмендегі деректерді толтырыңыз."},
    "Cinematic close-up of professional barber tools on a table": {
        "ru": "Крупный план профессиональных инструментов барбера на столе",
        "kk": "Үстелдегі кәсіби барбер құралдарының жақын көрінісі"},
    "Booking as": {"ru": "Вы записываетесь как", "kk": "Жазылушы"},
    "Choose a barber": {"ru": "Выберите барбера", "kk": "Барберді таңдаңыз"},
    "Choose services (one or more)": {"ru": "Выберите услуги (одну или несколько)",
                                      "kk": "Қызметтерді таңдаңыз (бір немесе бірнеше)"},
    "Confirm booking": {"ru": "Подтвердить запись", "kk": "Жазылуды растау"},
    "Your bookings": {"ru": "Ваши записи", "kk": "Сіздің жазылуларыңыз"},
    "My appointments": {"ru": "Мои записи", "kk": "Менің жазылуларым"},
    "Upcoming visits can be cancelled here.": {"ru": "Здесь можно отменить предстоящие визиты.",
                                               "kk": "Алдағы келулерден осы жерде бас тартуға болады."},
    "Cancel your appointment on {date} at {time}?": {
        "ru": "Отменить запись на {date} в {time}?",
        "kk": "{date}, {time} уақытындағы жазылудан бас тартасыз ба?"},
    "You have no upcoming appointments. Book one above.": {
        "ru": "У вас нет предстоящих записей. Запишитесь выше.",
        "kk": "Сізде алдағы жазылулар жоқ. Жоғарыда жазыла аласыз."},
    "History": {"ru": "История", "kk": "Тарих"},
    "Past and cancelled": {"ru": "Прошедшие и отменённые", "kk": "Өткен және бас тартылған"},
    "Your opinion": {"ru": "Ваше мнение", "kk": "Сіздің пікіріңіз"},
    "Rate your barber": {"ru": "Оцените барбера", "kk": "Барберді бағалаңыз"},
    "Choose from 1 to 5 stars. You can change your rating at any time.": {
        "ru": "Поставьте от 1 до 5 звёзд. Оценку можно изменить в любое время.",
        "kk": "1-ден 5-ке дейін жұлдыз қойыңыз. Бағаны кез келген уақытта өзгертуге болады."},
    "Average: ★ {avg}": {"ru": "Средняя: ★ {avg}", "kk": "Орташа: ★ {avg}"},
    "Your rating: {rating}/5": {"ru": "Ваша оценка: {rating}/5", "kk": "Сіздің бағаңыз: {rating}/5"},
    "Rate {n} out of 5": {"ru": "Оценить на {n} из 5", "kk": "5-тен {n} деп бағалау"},
    "After a visit is marked as completed, you can rate that barber here.": {
        "ru": "Когда визит будет отмечен как выполненный, здесь можно будет оценить барбера.",
        "kk": "Келу аяқталды деп белгіленгеннен кейін барберді осы жерде бағалай аласыз."},
    "At least 8 characters, with letters and digits.": {
        "ru": "Не менее 8 символов, буквы и цифры.",
        "kk": "Кемінде 8 таңба, әріптер мен сандар."},
    "Student project · Assignment 3": {"ru": "Студенческий проект · Задание 3",
                                       "kk": "Студенттік жоба · 3-тапсырма"},

    # ---------- Booking form texts (_booking.html, used by the JS) ----------
    "Choose services": {"ru": "Выберите услуги", "kk": "Қызметтерді таңдаңыз"},
    "Select time": {"ru": "Выберите время", "kk": "Уақытты таңдаңыз"},
    "Total: {minutes} min · {price} KZT": {"ru": "Итого: {minutes} мин · {price} ₸",
                                           "kk": "Барлығы: {minutes} мин · {price} ₸"},
    "No services selected yet.": {"ru": "Услуги пока не выбраны.", "kk": "Әзірге қызмет таңдалмаған."},
    "Your visit: {start}–{end}": {"ru": "Ваш визит: {start}–{end}", "kk": "Келу уақытыңыз: {start}–{end}"},
    "Choose a barber and a date to see free times.": {
        "ru": "Выберите барбера и дату, чтобы увидеть свободное время.",
        "kk": "Бос уақытты көру үшін барбер мен күнді таңдаңыз."},
    "Checking free times...": {"ru": "Проверяем свободное время...", "kk": "Бос уақыт тексерілуде..."},
    "Could not load free times. The server will still check your choice.": {
        "ru": "Не удалось загрузить свободное время. Сервер всё равно проверит ваш выбор.",
        "kk": "Бос уақытты жүктеу мүмкін болмады. Сервер таңдауыңызды бәрібір тексереді."},
    "Available start times: {count} (for {minutes} min). Unavailable times are greyed out.": {
        "ru": "Свободных вариантов начала: {count} (на {minutes} мин). Занятое время выделено серым.",
        "kk": "Бос басталу уақыттары: {count} ({minutes} мин). Бос емес уақыттар сұр түспен көрсетілген."},
    "These services together take {minutes} min — no time left with this barber on this date; remove a service or pick another day.": {
        "ru": "Эти услуги вместе займут {minutes} мин — у этого барбера в этот день столько времени нет. Уберите услугу или выберите другой день.",
        "kk": "Бұл қызметтер бірге {minutes} мин алады — бұл барберде осы күні мұндай бос уақыт жоқ. Бір қызметті алып тастаңыз немесе басқа күнді таңдаңыз."},
    "No free time with this barber on this date. Try another date or barber.": {
        "ru": "У этого барбера в этот день нет свободного времени. Выберите другую дату или барбера.",
        "kk": "Бұл барберде осы күні бос уақыт жоқ. Басқа күнді немесе барберді таңдаңыз."},
    "{time} does not fit the chosen services any more. Please pick another time.": {
        "ru": "Время {time} больше не подходит для выбранных услуг. Выберите другое время.",
        "kk": "{time} уақыты таңдалған қызметтерге енді сәйкес келмейді. Басқа уақытты таңдаңыз."},
    "booked": {"ru": "занято", "kk": "бос емес"},
    "barber is off": {"ru": "у барбера перерыв", "kk": "барбер демалыста"},
    "not working": {"ru": "нерабочее время", "kk": "жұмыс уақыты емес"},
    "past": {"ru": "прошло", "kk": "өтіп кетті"},
    "not enough time (shift ends {work_end})": {
        "ru": "не хватает времени (смена до {work_end})",
        "kk": "уақыт жетпейді (ауысым соңы — {work_end})"},
    "not enough time (overlaps the next booking)": {
        "ru": "не хватает времени (пересекается со следующей записью)",
        "kk": "уақыт жетпейді (келесі жазылумен қабаттасады)"},

    # ---------- Welcome page (auth.html) ----------
    "Welcome": {"ru": "Добро пожаловать", "kk": "Қош келдіңіз"},
    "To book an appointment you need an account.": {
        "ru": "Чтобы записаться, нужен аккаунт.",
        "kk": "Жазылу үшін тіркелгі қажет."},
    "Register": {"ru": "Регистрация", "kk": "Тіркелу"},
    "Create a password": {"ru": "Придумайте пароль", "kk": "Құпиясөз ойлап табыңыз"},
    "At least 8 characters, with both letters and digits.": {
        "ru": "Не менее 8 символов, обязательно буквы и цифры.",
        "kk": "Кемінде 8 таңба, әріптер де, сандар да болуы керек."},
    "Create account": {"ru": "Создать аккаунт", "kk": "Тіркелгі құру"},
    "Barber accounts are created by the administrator. Use the password you were given.": {
        "ru": "Аккаунты барберов создаёт администратор. Используйте выданный вам пароль.",
        "kk": "Барберлердің тіркелгісін әкімші жасайды. Сізге берілген құпиясөзді пайдаланыңыз."},
    "Log in as barber": {"ru": "Войти как барбер", "kk": "Барбер ретінде кіру"},
    "Digits only. Letters and symbols are not allowed here.": {
        "ru": "Только цифры. Буквы и символы здесь не допускаются.",
        "kk": "Тек сандар. Мұнда әріп пен таңба енгізуге болмайды."},
    "Letters only. Digits, spaces and symbols are not allowed here.": {
        "ru": "Только буквы. Цифры, пробелы и символы здесь не допускаются.",
        "kk": "Тек әріптер. Мұнда сан, бос орын және таңба енгізуге болмайды."},

    # ---------- Admin login (login.html) ----------
    "Restricted access": {"ru": "Доступ ограничен", "kk": "Шектеулі қолжетімділік"},
    "Username": {"ru": "Имя пользователя", "kk": "Пайдаланушы аты"},

    # ---------- Barber page (barber.html) ----------
    "Date: {date} · Working hours: {start}–{end}": {
        "ru": "Дата: {date} · Рабочие часы: {start}–{end}",
        "kk": "Күні: {date} · Жұмыс уақыты: {start}–{end}"},
    "My appointments: {count}": {"ru": "Мои записи: {count}", "kk": "Менің жазылуларым: {count}"},
    "My rating:": {"ru": "Мой рейтинг:", "kk": "Менің рейтингім:"},
    "Blocked by the administrator": {"ru": "Заблокировано администратором", "kk": "Әкімші бұғаттаған"},
    "Set by the administrator": {"ru": "Задано администратором", "kk": "Әкімші белгілеген"},
    "My time off": {"ru": "Мои перерывы", "kk": "Менің демалысым"},
    "At least 8 characters.": {"ru": "Не менее 8 символов.", "kk": "Кемінде 8 таңба."},

    # ---------- Admin page (admin.html) ----------
    "Today's appointments: {count}": {"ru": "Записи на сегодня: {count}",
                                      "kk": "Бүгінгі жазылулар: {count}"},
    "Remove break": {"ru": "Удалить перерыв", "kk": "Үзілісті жою"},
    "Front desk": {"ru": "Ресепшн", "kk": "Ресепшн"},
    "Add an appointment": {"ru": "Добавить запись", "kk": "Жазылу қосу"},
    "Book for a registered client, or for a walk-in client without an account (name only, phone is optional).": {
        "ru": "Запишите зарегистрированного клиента или клиента без аккаунта (нужно только имя, телефон — по желанию).",
        "kk": "Тіркелген клиентті немесе тіркелгісі жоқ клиентті жазыңыз (тек аты қажет, телефон міндетті емес)."},
    "Client": {"ru": "Клиент", "kk": "Клиент"},
    "Client type": {"ru": "Тип клиента", "kk": "Клиент түрі"},
    "Existing client": {"ru": "Существующий клиент", "kk": "Тіркелген клиент"},
    "New walk-in (no account)": {"ru": "Новый клиент без записи (без аккаунта)",
                                 "kk": "Жаңа клиент (тіркелгісіз)"},
    "Choose client": {"ru": "Выберите клиента", "kk": "Клиентті таңдаңыз"},
    "Select client": {"ru": "Выберите клиента", "kk": "Клиентті таңдаңыз"},
    "Client name": {"ru": "Имя клиента", "kk": "Клиенттің аты"},
    "e.g., Aidar": {"ru": "например, Айдар", "kk": "мысалы, Айдар"},
    "Phone (optional)": {"ru": "Телефон (необязательно)", "kk": "Телефон (міндетті емес)"},
    "digits only": {"ru": "только цифры", "kk": "тек сандар"},
    "Services (one or more)": {"ru": "Услуги (одна или несколько)", "kk": "Қызметтер (бір немесе бірнеше)"},
    "Add appointment": {"ru": "Добавить запись", "kk": "Жазылу қосу"},
    "Time off": {"ru": "Перерывы", "kk": "Демалыс"},
    "Barber breaks & days off": {"ru": "Перерывы и выходные барберов",
                                 "kk": "Барберлердің үзілістері мен демалыс күндері"},
    "Block a few hours, several days, or weeks. Clients cannot book blocked hours.": {
        "ru": "Заблокируйте несколько часов, дней или недель. На заблокированные часы клиенты записаться не смогут.",
        "kk": "Бірнеше сағатты, күнді немесе аптаны бұғаттаңыз. Бұғатталған сағаттарға клиенттер жазыла алмайды."},
    "From date": {"ru": "С даты", "kk": "Басталу күні"},
    "From time": {"ru": "С времени", "kk": "Басталу уақыты"},
    "To date": {"ru": "По дату", "kk": "Аяқталу күні"},
    "To time": {"ru": "До времени", "kk": "Аяқталу уақыты"},
    "Full days (all working hours, from the first date to the last date)": {
        "ru": "Целые дни (все рабочие часы с первой по последнюю дату)",
        "kk": "Толық күндер (бірінші күннен соңғы күнге дейінгі барлық жұмыс сағаттары)"},
    "Block time": {"ru": "Заблокировать время", "kk": "Уақытты бұғаттау"},
    "Remove this time off?": {"ru": "Удалить этот перерыв?", "kk": "Бұл демалысты жою керек пе?"},
    "Remove": {"ru": "Удалить", "kk": "Жою"},
    "No upcoming time off.": {"ru": "Предстоящих перерывов нет.", "kk": "Алдағы демалыс жоқ."},
    "Team management": {"ru": "Управление командой", "kk": "Команданы басқару"},
    "Manage barbers": {"ru": "Барберы", "kk": "Барберлерді басқару"},
    "e.g., John": {"ru": "например, Иван", "kk": "мысалы, Нұрлан"},
    "e.g., Doe": {"ru": "например, Иванов", "kk": "мысалы, Сейітов"},
    "Barber password": {"ru": "Пароль барбера", "kk": "Барбердің құпиясөзі"},
    "At least 8 characters": {"ru": "Не менее 8 символов", "kk": "Кемінде 8 таңба"},
    "Start time": {"ru": "Начало работы", "kk": "Жұмыс басталуы"},
    "End time": {"ru": "Конец работы", "kk": "Жұмыс аяқталуы"},
    "Add barber": {"ru": "Добавить барбера", "kk": "Барбер қосу"},
    "Hours: {start}–{end}": {"ru": "Часы работы: {start}–{end}", "kk": "Жұмыс уақыты: {start}–{end}"},
    "Rating:": {"ru": "Рейтинг:", "kk": "Рейтинг:"},
    "Login password: set": {"ru": "Пароль для входа: задан", "kk": "Кіру құпиясөзі: орнатылған"},
    "No password - cannot log in": {"ru": "Нет пароля — вход невозможен",
                                    "kk": "Құпиясөз жоқ — кіру мүмкін емес"},
    "Set password": {"ru": "Задать пароль", "kk": "Құпиясөз орнату"},
    "Are you sure you want to delete {name}?": {"ru": "Удалить барбера {name}?",
                                                "kk": "{name} барберін шынымен жою керек пе?"},
    "Delete": {"ru": "Удалить", "kk": "Жою"},

    # ---------- Validation (app.py) ----------
    "{label} is required.": {"ru": "Заполните поле «{label}».", "kk": "«{label}» өрісін толтырыңыз."},
    "{label} is too long (maximum 50 letters).": {
        "ru": "Поле «{label}» слишком длинное (не более 50 букв).",
        "kk": "«{label}» өрісі тым ұзын (ең көбі 50 әріп)."},
    "{label}: only letters are allowed (no digits, spaces or symbols).": {
        "ru": "«{label}»: допускаются только буквы (без цифр, пробелов и символов).",
        "kk": "«{label}»: тек әріп енгізуге болады (сан, бос орын және таңба енгізуге болмайды)."},
    "Barber first name": {"ru": "Имя барбера", "kk": "Барбердің аты"},
    "Barber last name": {"ru": "Фамилия барбера", "kk": "Барбердің тегі"},
    "Phone number is required.": {"ru": "Укажите номер телефона.", "kk": "Телефон нөмірін енгізіңіз."},
    "Phone number: only digits are allowed (no letters or symbols).": {
        "ru": "Номер телефона: допускаются только цифры (без букв и символов).",
        "kk": "Телефон нөмірі: тек сан енгізуге болады (әріп пен таңба енгізуге болмайды)."},
    "Phone number must be between 10 and 15 digits long.": {
        "ru": "Номер телефона должен содержать от 10 до 15 цифр.",
        "kk": "Телефон нөмірі 10-нан 15-ке дейінгі саннан тұруы керек."},
    "Password must be at least 8 characters long.": {
        "ru": "Пароль должен содержать не менее 8 символов.",
        "kk": "Құпиясөз кемінде 8 таңбадан тұруы керек."},
    "Password is too long (maximum 128 characters).": {
        "ru": "Пароль слишком длинный (не более 128 символов).",
        "kk": "Құпиясөз тым ұзын (ең көбі 128 таңба)."},
    "Password must contain at least one letter.": {
        "ru": "Пароль должен содержать хотя бы одну букву.",
        "kk": "Құпиясөзде кемінде бір әріп болуы керек."},
    "Password must contain at least one digit.": {
        "ru": "Пароль должен содержать хотя бы одну цифру.",
        "kk": "Құпиясөзде кемінде бір сан болуы керек."},

    # ---------- Time checks (app.py: fits) ----------
    "This time has already passed. Please choose a later time.": {
        "ru": "Это время уже прошло. Выберите более позднее время.",
        "kk": "Бұл уақыт өтіп кетті. Кейінірек уақытты таңдаңыз."},
    "The barber works {start}–{end}. Please choose a time in that range.": {
        "ru": "Барбер работает с {start} до {end}. Выберите время в этом промежутке.",
        "kk": "Барбер {start}–{end} аралығында жұмыс істейді. Осы аралықтағы уақытты таңдаңыз."},
    "The barber is off at this time. Please choose another time.": {
        "ru": "В это время у барбера перерыв. Выберите другое время.",
        "kk": "Бұл уақытта барбер демалыста. Басқа уақытты таңдаңыз."},
    "This time is already booked by another client. Please choose another time.": {
        "ru": "Это время уже занято другим клиентом. Выберите другое время.",
        "kk": "Бұл уақытқа басқа клиент жазылып қойған. Басқа уақытты таңдаңыз."},
    "Not enough time: the selected services take {total} min ({start}–{end}), but the barber's shift ends at {work_end}. Choose an earlier time or fewer services.": {
        "ru": "Не хватает времени: выбранные услуги займут {total} мин ({start}–{end}), а смена барбера заканчивается в {work_end}. Выберите более раннее время или меньше услуг.",
        "kk": "Уақыт жетпейді: таңдалған қызметтер {total} мин алады ({start}–{end}), ал барбердің ауысымы {work_end} уақытында аяқталады. Ертерек уақытты немесе азырақ қызметті таңдаңыз."},
    "Not enough time: the selected services take {total} min ({start}–{end}) and would overlap the next booking at {at}. Choose another time or fewer services.": {
        "ru": "Не хватает времени: выбранные услуги займут {total} мин ({start}–{end}) и пересекутся со следующей записью в {at}. Выберите другое время или меньше услуг.",
        "kk": "Уақыт жетпейді: таңдалған қызметтер {total} мин алады ({start}–{end}) және {at} уақытындағы келесі жазылумен қабаттасады. Басқа уақытты немесе азырақ қызметті таңдаңыз."},
    "Not enough time: the selected services take {total} min ({start}–{end}) and would overlap the barber's time off at {at}. Choose another time or fewer services.": {
        "ru": "Не хватает времени: выбранные услуги займут {total} мин ({start}–{end}) и пересекутся с перерывом барбера в {at}. Выберите другое время или меньше услуг.",
        "kk": "Уақыт жетпейді: таңдалған қызметтер {total} мин алады ({start}–{end}) және барбердің {at} уақытындағы демалысымен қабаттасады. Басқа уақытты немесе азырақ қызметті таңдаңыз."},
    "Please choose valid services.": {"ru": "Выберите корректные услуги.", "kk": "Дұрыс қызметтерді таңдаңыз."},
    "Please choose at least one service.": {"ru": "Выберите хотя бы одну услугу.",
                                            "kk": "Кемінде бір қызметті таңдаңыз."},
    "Please choose at most {max} services in one booking.": {
        "ru": "В одной записи можно выбрать не более {max} услуг.",
        "kk": "Бір жазылуда ең көбі {max} қызмет таңдауға болады."},
    "One of the selected services does not exist.": {"ru": "Одной из выбранных услуг не существует.",
                                                     "kk": "Таңдалған қызметтердің бірі жоқ."},

    # ---------- Flash messages: customer ----------
    "Please log in or register to continue.": {
        "ru": "Войдите или зарегистрируйтесь, чтобы продолжить.",
        "kk": "Жалғастыру үшін кіріңіз немесе тіркеліңіз."},
    "Please fill in all fields.": {"ru": "Заполните все поля.", "kk": "Барлық өрістерді толтырыңыз."},
    "Please enter a valid date, time, barber and service.": {
        "ru": "Укажите корректные дату, время, барбера и услугу.",
        "kk": "Күнді, уақытты, барберді және қызметті дұрыс көрсетіңіз."},
    "Please choose a start time from the list (every 30 minutes).": {
        "ru": "Выберите время начала из списка (каждые 30 минут).",
        "kk": "Басталу уақытын тізімнен таңдаңыз (әр 30 минут сайын)."},
    "The selected barber does not exist.": {"ru": "Выбранный барбер не найден.",
                                            "kk": "Таңдалған барбер табылмады."},
    "Appointment booked successfully! {date}, {start}–{end}: {services} ({minutes} min).": {
        "ru": "Вы успешно записаны! {date}, {start}–{end}: {services} ({minutes} мин).",
        "kk": "Сіз сәтті жазылдыңыз! {date}, {start}–{end}: {services} ({minutes} мин)."},
    "The backup could not be created.": {"ru": "Не удалось создать резервную копию.",
                                         "kk": "Сақтық көшірме жасалмады."},
    "This time may already be booked, or the submitted data violates a database rule.": {
        "ru": "Возможно, это время уже занято, или отправленные данные нарушают правило базы данных.",
        "kk": "Бұл уақыт бос емес болуы мүмкін немесе жіберілген деректер дерекқор ережесін бұзады."},
    "A database error occurred. Check the VS Code terminal.": {
        "ru": "Произошла ошибка базы данных. Проверьте терминал VS Code.",
        "kk": "Дерекқор қатесі орын алды. VS Code терминалын тексеріңіз."},
    "Please choose from 1 to 5 stars.": {"ru": "Выберите от 1 до 5 звёзд.",
                                         "kk": "1-ден 5-ке дейін жұлдыз таңдаңыз."},
    "This barber does not exist.": {"ru": "Такого барбера нет.", "kk": "Мұндай барбер жоқ."},
    "You can rate a barber after a completed visit.": {
        "ru": "Оценить барбера можно только после завершённого визита.",
        "kk": "Барберді аяқталған келуден кейін ғана бағалауға болады."},
    "Thank you! You rated {name}: {rating}/5.": {
        "ru": "Спасибо! Ваша оценка барбера {name}: {rating}/5.",
        "kk": "Рақмет! {name} барберіне қойған бағаңыз: {rating}/5."},
    "Database error occurred. Please try again.": {"ru": "Ошибка базы данных. Попробуйте ещё раз.",
                                                   "kk": "Дерекқор қатесі. Қайталап көріңіз."},
    "Your appointment has been cancelled.": {"ru": "Ваша запись отменена.",
                                             "kk": "Жазылудан бас тартылды."},
    "This appointment cannot be cancelled.": {"ru": "Эту запись нельзя отменить.",
                                              "kk": "Бұл жазылудан бас тарту мүмкін емес."},
    "This phone number cannot be used.": {"ru": "Этот номер телефона использовать нельзя.",
                                          "kk": "Бұл телефон нөмірін пайдалануға болмайды."},
    "An account with this phone number already exists. Please log in.": {
        "ru": "Аккаунт с этим номером телефона уже существует. Пожалуйста, войдите.",
        "kk": "Бұл телефон нөмірімен тіркелгі бұрыннан бар. Кіріңіз."},
    "Account created. Welcome, {name}!": {"ru": "Аккаунт создан. Добро пожаловать, {name}!",
                                          "kk": "Тіркелгі құрылды. Қош келдіңіз, {name}!"},
    "Please enter your phone number and password.": {"ru": "Введите номер телефона и пароль.",
                                                     "kk": "Телефон нөмірі мен құпиясөзді енгізіңіз."},
    "Invalid phone number or password.": {"ru": "Неверный номер телефона или пароль.",
                                          "kk": "Телефон нөмірі немесе құпиясөз қате."},
    "Please fill in all three password fields.": {"ru": "Заполните все три поля пароля.",
                                                  "kk": "Құпиясөздің үш өрісін де толтырыңыз."},
    "The current password is wrong.": {"ru": "Текущий пароль неверен.", "kk": "Ағымдағы құпиясөз қате."},
    "The new password and its confirmation do not match.": {
        "ru": "Новый пароль и подтверждение не совпадают.",
        "kk": "Жаңа құпиясөз бен оның растауы сәйкес келмейді."},
    "The new password must be different from the current one.": {
        "ru": "Новый пароль должен отличаться от текущего.",
        "kk": "Жаңа құпиясөз ағымдағысынан өзгеше болуы керек."},
    "Password changed successfully.": {"ru": "Пароль успешно изменён.", "kk": "Құпиясөз сәтті өзгертілді."},

    # ---------- Flash messages: barber / admin login ----------
    "Please log in to access the dashboard.": {"ru": "Войдите, чтобы открыть панель управления.",
                                               "kk": "Басқару тақтасын ашу үшін кіріңіз."},
    "Please log in as a barber to access this page.": {
        "ru": "Войдите как барбер, чтобы открыть эту страницу.",
        "kk": "Бұл бетті ашу үшін барбер ретінде кіріңіз."},
    "Please enter your first name, last name and password.": {
        "ru": "Введите имя, фамилию и пароль.",
        "kk": "Атыңызды, тегіңізді және құпиясөзді енгізіңіз."},
    "Invalid name or password. Barber passwords are assigned by the administrator.": {
        "ru": "Неверное имя или пароль. Пароли барберам выдаёт администратор.",
        "kk": "Аты немесе құпиясөз қате. Барберлердің құпиясөзін әкімші береді."},
    "Welcome to the Admin Dashboard.": {"ru": "Добро пожаловать в панель администратора.",
                                        "kk": "Әкімші тақтасына қош келдіңіз."},
    "Invalid credentials. Please try again.": {"ru": "Неверный логин или пароль. Попробуйте ещё раз.",
                                               "kk": "Логин немесе құпиясөз қате. Қайталап көріңіз."},
    "You have been logged out.": {"ru": "Вы вышли из аккаунта.", "kk": "Сіз жүйеден шықтыңыз."},
    "Invalid status update.": {"ru": "Недопустимое изменение статуса.",
                               "kk": "Мәртебені бұлай өзгертуге болмайды."},
    "Appointment #{id} marked as {status}.": {"ru": "Запись №{id}: статус «{status}».",
                                              "kk": "№{id} жазылу мәртебесі: «{status}»."},
    "Appointment not found.": {"ru": "Запись не найдена.", "kk": "Жазылу табылмады."},
    "Database error occurred while updating status.": {
        "ru": "Ошибка базы данных при изменении статуса.",
        "kk": "Мәртебені өзгерту кезінде дерекқор қатесі орын алды."},
    "Appointment #{id} cannot be restored. {reason}": {
        "ru": "Запись №{id} нельзя восстановить. {reason}",
        "kk": "№{id} жазылуды қалпына келтіру мүмкін емес. {reason}"},

    # ---------- Flash messages: admin ----------
    "Please choose a barber, at least one service, a date and a time.": {
        "ru": "Выберите барбера, хотя бы одну услугу, дату и время.",
        "kk": "Барберді, кемінде бір қызметті, күн мен уақытты таңдаңыз."},
    "Please enter a valid date and time.": {"ru": "Укажите корректные дату и время.",
                                            "kk": "Күн мен уақытты дұрыс көрсетіңіз."},
    "You cannot add an appointment for a day that has passed.": {
        "ru": "Нельзя добавить запись на прошедший день.",
        "kk": "Өткен күнге жазылу қосуға болмайды."},
    "Please enter the client's name.": {"ru": "Введите имя клиента.", "kk": "Клиенттің атын енгізіңіз."},
    "Client name: only letters are allowed (no digits or symbols).": {
        "ru": "Имя клиента: допускаются только буквы (без цифр и символов).",
        "kk": "Клиенттің аты: тек әріп енгізуге болады (сан мен таңба енгізуге болмайды)."},
    "Please choose a client from the list.": {"ru": "Выберите клиента из списка.",
                                              "kk": "Клиентті тізімнен таңдаңыз."},
    "The selected client does not exist.": {"ru": "Выбранный клиент не найден.",
                                            "kk": "Таңдалған клиент табылмады."},
    "Appointment added for {client}: {date}, {start}–{end} with {barber} ({services}, {minutes} min).": {
        "ru": "Запись добавлена: {client}, {date}, {start}–{end}, барбер {barber} ({services}, {minutes} мин).",
        "kk": "Жазылу қосылды: {client}, {date}, {start}–{end}, барбер {barber} ({services}, {minutes} мин)."},
    "That time is already taken for this barber (booking or break).": {
        "ru": "Это время у барбера уже занято (запись или перерыв).",
        "kk": "Барбердің бұл уақыты бос емес (жазылу немесе үзіліс)."},
    "Database error occurred while adding the appointment.": {
        "ru": "Ошибка базы данных при добавлении записи.",
        "kk": "Жазылуды қосу кезінде дерекқор қатесі орын алды."},
    "Please choose a barber and the start and end dates.": {
        "ru": "Выберите барбера и даты начала и окончания.",
        "kk": "Барберді және басталу мен аяқталу күндерін таңдаңыз."},
    "Please choose the start and end time, or tick 'Full days'.": {
        "ru": "Выберите время начала и окончания или отметьте «Целые дни».",
        "kk": "Басталу және аяқталу уақытын таңдаңыз немесе «Толық күндер» белгісін қойыңыз."},
    "Please enter valid dates and times.": {"ru": "Укажите корректные даты и время.",
                                            "kk": "Күндер мен уақытты дұрыс көрсетіңіз."},
    "Time off cannot start on a day that has already passed.": {
        "ru": "Перерыв не может начинаться в прошедший день.",
        "kk": "Демалыс өткен күннен басталмайды."},
    "The end of the time off must be after its start.": {
        "ru": "Конец перерыва должен быть позже его начала.",
        "kk": "Демалыстың аяқталуы басталуынан кейін болуы керек."},
    "Time off can be at most one year long.": {"ru": "Перерыв может длиться не больше года.",
                                               "kk": "Демалыс ең көбі бір жылға созылады."},
    "No working hours of {name} ({start}–{end}) fall inside this period.": {
        "ru": "В этот период нет рабочих часов барбера {name} ({start}–{end}).",
        "kk": "Бұл кезеңге {name} барберінің жұмыс сағаттары ({start}–{end}) түспейді."},
    "{start} to {end}": {"ru": "с {start} по {end}", "kk": "{start} – {end}"},
    "{start} to {end} (full days)": {"ru": "с {start} по {end} (целые дни)",
                                     "kk": "{start} – {end} (толық күндер)"},
    "Time off added for {name}: {blocked}, {period}.": {
        "ru": "Перерыв для барбера {name} добавлен: {blocked}, {period}.",
        "kk": "{name} барберіне демалыс қосылды: {blocked}, {period}."},
    "Database error occurred while adding time off.": {
        "ru": "Ошибка базы данных при добавлении перерыва.",
        "kk": "Демалысты қосу кезінде дерекқор қатесі орын алды."},
    "Time off removed. The hours are open for booking again.": {
        "ru": "Перерыв удалён. На эти часы снова можно записаться.",
        "kk": "Демалыс жойылды. Бұл сағаттарға қайтадан жазылуға болады."},
    "Database error occurred while removing time off.": {
        "ru": "Ошибка базы данных при удалении перерыва.",
        "kk": "Демалысты жою кезінде дерекқор қатесі орын алды."},
    "All fields are required to add a barber.": {"ru": "Чтобы добавить барбера, заполните все поля.",
                                                 "kk": "Барберді қосу үшін барлық өрістерді толтырыңыз."},
    "Work end time must be after start time.": {
        "ru": "Время окончания работы должно быть позже времени начала.",
        "kk": "Жұмыстың аяқталу уақыты басталу уақытынан кейін болуы керек."},
    "A barber with this name already exists.": {"ru": "Барбер с таким именем уже есть.",
                                                "kk": "Осындай аты бар барбер бұрыннан бар."},
    "Barber {name} added successfully!": {"ru": "Барбер {name} успешно добавлен!",
                                          "kk": "{name} барбері сәтті қосылды!"},
    "Database error occurred while adding barber.": {"ru": "Ошибка базы данных при добавлении барбера.",
                                                     "kk": "Барберді қосу кезінде дерекқор қатесі орын алды."},
    "Barber password saved.": {"ru": "Пароль барбера сохранён.", "kk": "Барбердің құпиясөзі сақталды."},
    "Barber not found.": {"ru": "Барбер не найден.", "kk": "Барбер табылмады."},
    "Database error occurred while saving the password.": {
        "ru": "Ошибка базы данных при сохранении пароля.",
        "kk": "Құпиясөзді сақтау кезінде дерекқор қатесі орын алды."},
    "Break removed successfully! The time slot is now open for customers.": {
        "ru": "Перерыв удалён! Это время снова доступно клиентам.",
        "kk": "Үзіліс сәтті жойылды! Бұл уақыт енді клиенттерге ашық."},
    "Database error occurred while removing break.": {"ru": "Ошибка базы данных при удалении перерыва.",
                                                      "kk": "Үзілісті жою кезінде дерекқор қатесі орын алды."},
    "Cannot delete this barber because they have booked appointments in the system.": {
        "ru": "Нельзя удалить этого барбера: в системе есть его записи.",
        "kk": "Бұл барберді жою мүмкін емес: жүйеде оның жазылулары бар."},
    "Barber removed successfully.": {"ru": "Барбер удалён.", "kk": "Барбер сәтті жойылды."},
    "Database error occurred while deleting barber.": {"ru": "Ошибка базы данных при удалении барбера.",
                                                       "kk": "Барберді жою кезінде дерекқор қатесі орын алды."},
}
