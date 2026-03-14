from __future__ import annotations 

import asyncio 
import html 
import logging 
from datetime import datetime 

from aiogram import Bot ,Dispatcher ,F 
from aiogram .client .default import DefaultBotProperties 
from aiogram .enums import ParseMode 
from aiogram .exceptions import TelegramBadRequest 
from aiogram .filters import Command ,CommandStart 
from aiogram .fsm .context import FSMContext 
from aiogram .fsm .state import State ,StatesGroup 
from aiogram .fsm .storage .memory import MemoryStorage 
from aiogram .types import (
BotCommand ,
CallbackQuery ,
InlineKeyboardButton ,
InlineKeyboardMarkup ,
Message ,
)
from aiogram .utils .keyboard import InlineKeyboardBuilder 

from app .config import settings 
from app .db import SessionLocal 
from app .scheduler import restart_scheduler 
from app .services import AccountService ,AppSettingsService ,BindingService ,ChatAutomationService 


logger =logging .getLogger (__name__ )


class WizardStates (StatesGroup ):
    waiting_phone =State ()
    waiting_proxy =State ()
    waiting_code =State ()
    waiting_password =State ()
    waiting_chat_ref =State ()
    waiting_interval =State ()


class BindChatStates (StatesGroup ):
    waiting_account_id =State ()
    waiting_chat_ref =State ()
    waiting_interval =State ()


class BindingSettingsStates (StatesGroup ):
    waiting_prompt =State ()
    waiting_interval =State ()
    waiting_reply_interval =State ()
    waiting_context =State ()


class MainSettingsStates (StatesGroup ):
    waiting_prompt =State ()
    waiting_model =State ()


def main_menu_keyboard ()->InlineKeyboardMarkup :
    builder =InlineKeyboardBuilder ()
    builder .button (text ='Мастер',callback_data ="menu:wizard")
    builder .button (text ='Привязать чат',callback_data ="menu:bind_chat_start")
    builder .button (text ="РРЅСЃС‚СЂСѓРєС†РёСЏ",callback_data ="menu:help")
    builder .button (text ='Аккаунты',callback_data ="menu:accounts")
    builder .button (text ='Чаты',callback_data ="menu:chats")
    builder .button (text ='Проверить аккаунты',callback_data ="menu:audit")
    builder .button (text ='Статус отправки',callback_data ="menu:status")
    builder .button (text ='Перезапустить задачи',callback_data ="menu:restart_runners")
    builder .button (text ='Основной промпт',callback_data ="menu:main_prompt")
    builder .button (text ='Модель',callback_data ="menu:model")
    builder .button (text ='Вернуться в начало',callback_data ="menu:back")
    builder .adjust (2 ,2 ,2 ,2 ,2 ,1 )
    return builder .as_markup ()


def main_prompt_keyboard ()->InlineKeyboardMarkup :
    builder =InlineKeyboardBuilder ()
    builder .button (text ="РР·РјРµРЅРёС‚СЊ РѕСЃРЅРѕРІРЅРѕР№ РїСЂРѕРјРїС‚",callback_data ="menu:main_prompt:set")
    builder .button (text ='Сбросить основной промпт',callback_data ="menu:main_prompt:reset")
    builder .button (text ='Вернуться в начало',callback_data ="menu:back")
    builder .adjust (1 )
    return builder .as_markup ()


def model_keyboard ()->InlineKeyboardMarkup :
    builder =InlineKeyboardBuilder ()
    builder .button (text ="РР·РјРµРЅРёС‚СЊ РјРѕРґРµР»СЊ",callback_data ="menu:model:set")
    builder .button (text ='Сбросить модель',callback_data ="menu:model:reset")
    builder .button (text ='Вернуться в начало',callback_data ="menu:back")
    builder .adjust (1 )
    return builder .as_markup ()


def binding_actions_keyboard (binding_id :int )->InlineKeyboardMarkup :
    return InlineKeyboardMarkup (
    inline_keyboard =[
    [InlineKeyboardButton (text =f"Удалить привязку {binding_id }",callback_data =f"binding:delete:{binding_id }")],
    [InlineKeyboardButton (text ='Настройки привязки',callback_data =f"binding:settings:{binding_id }")],
    [InlineKeyboardButton (text ='Обновить список',callback_data ="menu:chats")],
    [InlineKeyboardButton (text ='Вернуться в начало',callback_data ="menu:back")],
    ]
    )


def bindings_list_keyboard (bindings :list )->InlineKeyboardMarkup :
    builder =InlineKeyboardBuilder ()
    for b in bindings :
        builder .button (text =_binding_button_text (b ),callback_data =f"binding:settings:{b .id }")
    builder .button (text ='Вернуться в начало',callback_data ="menu:back")
    builder .adjust (1 )
    return builder .as_markup ()


def binding_settings_keyboard (binding_id :int )->InlineKeyboardMarkup :
    builder =InlineKeyboardBuilder ()
    builder .button (text ='Задать промпт',callback_data =f"binding:set_prompt:{binding_id }")
    builder .button (text ='Задать интервал',callback_data =f"binding:set_interval:{binding_id }")
    builder .button (text ="РРЅС‚РµСЂРІР°Р» РѕС‚РІРµС‚РѕРІ",callback_data =f"binding:set_reply_interval:{binding_id }")
    builder .button (text ='Количество сообщений',callback_data =f"binding:set_context:{binding_id }")
    builder .button (text ='Сгенерировать и отправить сообщение',callback_data =f"binding:send_once:{binding_id }")
    builder .button (text ='Удалить привязку',callback_data =f"binding:delete:{binding_id }")
    builder .button (text ='Назад к чатам',callback_data ="menu:chats")
    builder .button (text ='Вернуться в начало',callback_data ="menu:back")
    builder .adjust (1 )
    return builder .as_markup ()


def back_keyboard ()->InlineKeyboardMarkup :
    return InlineKeyboardMarkup (
    inline_keyboard =[[InlineKeyboardButton (text ='Вернуться в начало',callback_data ="menu:back")]]
    )


def format_help ()->str :
    return 'Справка\n\n- /wizard - пошаговое добавление аккаунта и привязки.\n- /accounts - список аккаунтов.\n- /chats - список привязок.\n- /send_status - статус отправок и автоответов.\n- /set_binding_interval <binding_id> <min> [max] - интервал отправки.\n- /set_binding_reply_interval <binding_id> <min> [max] | off - интервал автоответов.\n- /set_binding_context <binding_id> <count> - глубина контекста.\n- /set_binding_prompt <binding_id> <text> - промпт для чата.\n- /reset_binding_prompt <binding_id> - сброс промпта.\n- /create_group <account_id> <описание> - создать группу.\n\nОсновной промпт меняется через кнопку «Основной промпт».\nДля случайного интервала можно указать два числа, например `5 15`.\nДля отключения автоответов используй `off`.'


def format_audit_report (report :dict [str ,object ])->str :
    lines =[
    "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432:",
    f"audited={report .get ('audited',0 )}",
    f"active={report .get ('active',0 )}",
    f"inactive={report .get ('inactive',0 )}",
    f"paused_bindings={report .get ('paused_bindings',0 )}",
    f"resumed_bindings={report .get ('resumed_bindings',0 )}",
    "",
    ]
    details =report .get ("details",[])
    for item in details [:20 ]:
        lines .append (
        f"{item ['account_id']}: {item ['phone']} | {item ['auth_status']} | "
        f"active={int (bool (item ['is_active']))} | paused={item ['paused_bindings']} | "
        f"resumed={item ['resumed_bindings']} | {item ['reason']}"
        )
    return "\n".join (lines )


def _short_time (value :str |None )->str :
    if not value :
        return "-"
    try :
        dt =datetime .fromisoformat (value .replace ("Z","+00:00"))
    except ValueError :
        return value 
    return dt .strftime ("%Y-%m-%d %H:%M:%S")


def _prompt_preview (value :str |None ,limit :int =140 )->str :
    if not value :
        return "-"
    compact =" ".join (value .split ())
    if len (compact )<=limit :
        return compact 
    return compact [:limit ]+"..."


def format_main_prompt_settings (prompt :str ,is_custom :bool )->str :
    source ='пользовательский'if is_custom else 'по умолчанию'
    return (
    f"Основной системный промпт ({source }):\n"
    f"{_prompt_preview (prompt ,limit =1200 )}"
    )


def format_model_settings (model :str ,is_custom :bool )->str :
    source ='пользовательская'if is_custom else 'по умолчанию (.env)'
    return f"Текущая модель ({source }):\n<code>{html .escape (model )}</code>"


def _format_interval_range (interval_min :int |None ,interval_max :int |None )->str :
    if interval_min is None or interval_max is None :
        return "off"
    return f"{interval_min }-{interval_max }m"


def _truncate_label (value :str ,limit :int =60 )->str :
    if len (value )<=limit :
        return value 
    return f"{value [:limit -3 ]}..."


def _binding_account_name (binding :object )->str :
    account_name =getattr (binding ,"account_name",None )
    if account_name :
        return str (account_name )
    account =getattr (binding ,"account",None )
    if account is not None :
        if getattr (account ,"account_name",None ):
            return str (account .account_name )
        if getattr (account ,"phone",None ):
            return str (account .phone )
        if getattr (account ,"session_name",None ):
            return str (account .session_name )
    return str (getattr (binding ,"account_id","?"))


def _binding_chat_name (binding :object )->str :
    return str (getattr (binding ,"chat_title",None )or getattr (binding ,"chat_ref","unknown"))


def _binding_button_text (binding :object )->str :
    text =f"{binding .id } | {binding .account_id } | {_binding_account_name (binding )} | {_binding_chat_name (binding )}"
    return _truncate_label (text )


def _parse_reply_interval_input (text :str )->tuple [int |None ,int |None ,bool ]:
    value =text .strip ().lower ()
    if value in {"off","disable","disabled"}:
        return None ,None ,True 
    parts =value .split ()
    if not parts or len (parts )>2 or not all (part .isdigit ()for part in parts ):
        raise ValueError ("bad_format")
    interval_min =int (parts [0 ])
    interval_max =int (parts [1 ])if len (parts )>1 else interval_min 
    return interval_min ,interval_max ,False 


def format_send_status (items :list [dict [str ,object ]])->str :
    if not items :
        return "No bindings."
    lines =[]
    for item in items :
        lines .append (
        f"binding={item ['binding_id']} | acc={item ['account_id']} | state={item ['state']} | "
        f"chat={item ['chat_ref']} | next={_short_time (item ['next_run_at'])} | "
        f"range={_format_interval_range (item .get ('interval_min_minutes'),item .get ('interval_max_minutes'))} | "
        f"reply={_format_interval_range (item .get ('reply_interval_min_minutes'),item .get ('reply_interval_max_minutes'))} "
        f"({item .get ('reply_state','disabled')}) | reply_next={_short_time (item .get ('next_reply_run_at'))} | "
        f"ctx={item .get ('context_message_count','-')}"
        )
    return "\n".join (lines )


def format_binding (binding :object )->str :
    interval_min =getattr (binding ,"interval_min_minutes",getattr (binding ,"interval_minutes",None ))
    interval_max =getattr (binding ,"interval_max_minutes",getattr (binding ,"interval_minutes",None ))
    reply_interval_min =getattr (binding ,"reply_interval_min_minutes",None )
    reply_interval_max =getattr (binding ,"reply_interval_max_minutes",None )
    context_count =getattr (binding ,"context_message_count","-")
    prompt =getattr (binding ,"system_prompt",None )
    is_enabled =getattr (binding ,"is_enabled",True )
    auto_paused =getattr (binding ,"auto_paused",False )
    auto_pause_reason =getattr (binding ,"auto_pause_reason",None )
    status ="auto_paused"if auto_paused else ("enabled"if bool (is_enabled )else "disabled")
    if auto_paused and auto_pause_reason :
        status +=f"({auto_pause_reason })"
    return (
    f"{binding .id }: account_id={binding .account_id } account_name={_binding_account_name (binding )} chat={_binding_chat_name (binding )} ({binding .chat_ref })\n"
    f"interval={_format_interval_range (interval_min ,interval_max )} | reply_interval={_format_interval_range (reply_interval_min ,reply_interval_max )} | context={context_count } | status={status }\n"
    f"prompt={_prompt_preview (prompt )}"
    )


def format_binding_settings (binding :object )->str :
    interval_min =getattr (binding ,"interval_min_minutes",getattr (binding ,"interval_minutes",None ))
    interval_max =getattr (binding ,"interval_max_minutes",getattr (binding ,"interval_minutes",None ))
    reply_interval_min =getattr (binding ,"reply_interval_min_minutes",None )
    reply_interval_max =getattr (binding ,"reply_interval_max_minutes",None )
    context_count =getattr (binding ,"context_message_count","-")
    system_prompt =getattr (binding ,"system_prompt",None )or "-"
    last_posted_at =getattr (binding ,"last_posted_at",None )or "-"
    next_run_at =getattr (binding ,"next_run_at",None )or "-"
    last_reply_posted_at =getattr (binding ,"last_reply_posted_at",None )or "-"
    next_reply_run_at =getattr (binding ,"next_reply_run_at",None )or "-"
    auto_paused =getattr (binding ,"auto_paused",False )
    auto_pause_reason =getattr (binding ,"auto_pause_reason",None )or "-"
    auto_paused_at =getattr (binding ,"auto_paused_at",None )or "-"
    return (
    f"Binding settings {binding .id }:\n"
    f"account_name={_binding_account_name (binding )}\n"
    f"account_id={binding .account_id }\n"
    f"chat_title={_binding_chat_name (binding )}\n"
    f"chat_ref={binding .chat_ref }\n"
    f"interval={_format_interval_range (interval_min ,interval_max )}\n"
    f"reply_interval={_format_interval_range (reply_interval_min ,reply_interval_max )}\n"
    f"context_message_count={context_count }\n"
    f"auto_paused={int (bool (auto_paused ))}\n"
    f"auto_pause_reason={auto_pause_reason }\n"
    f"auto_paused_at={auto_paused_at }\n"
    f"system_prompt={system_prompt }\n"
    f"last_posted_at={last_posted_at }\n"
    f"next_run_at={next_run_at }\n"
    f"last_reply_posted_at={last_reply_posted_at }\n"
    f"next_reply_run_at={next_reply_run_at }"
    )


def _account_display_name (account :object )->str :
    account_name =getattr (account ,"account_name",None )
    if account_name :
        return str (account_name )
    return "Без имени"


def _account_button_text (account :object )->str :
    phone =str (getattr (account ,"phone","?"))
    text =f"{_account_display_name (account )} | {phone }"
    return _truncate_label (text )


def accounts_list_keyboard (accounts :list [object ])->InlineKeyboardMarkup :
    builder =InlineKeyboardBuilder ()
    for account in accounts :
        builder .button (text =_account_button_text (account ),callback_data =f"account:view:{account .id }")
    builder .button (text ="Вернуться в начало",callback_data ="menu:back")
    builder .adjust (1 )
    return builder .as_markup ()


def account_actions_keyboard (account_id :int )->InlineKeyboardMarkup :
    builder =InlineKeyboardBuilder ()
    builder .button (text ="Ручная проверка",callback_data =f"account:check:{account_id }")
    builder .button (text ="Удалить аккаунт",callback_data =f"account:delete:{account_id }")
    builder .button (text ="Назад к списку",callback_data ="menu:accounts")
    builder .button (text ="Вернуться в начало",callback_data ="menu:back")
    builder .adjust (1 )
    return builder .as_markup ()


def format_account_details (account :object )->str :
    character =getattr (account ,"character",None )
    character_name =getattr (character ,"name",None )or "Не назначен"
    return (
    f"Аккаунт {getattr (account ,'id','?')}:\n"
    f"Имя: {_account_display_name (account )}\n"
    f"Номер: {getattr (account ,'phone','-')}\n"
    f"Статус: {getattr (account ,'auth_status','-')}\n"
    f"Активен: {int (bool (getattr (account ,'is_active',False )))}\n"
    f"Персонаж: {character_name }"
    )


def build_bot ()->tuple [Bot ,Dispatcher ]:
    bot =Bot (token =settings .bot_token ,default =DefaultBotProperties (parse_mode =ParseMode .HTML ))
    dp =Dispatcher (storage =MemoryStorage ())

    async def set_bot_commands ()->None :
        await bot .set_my_commands (
        [
        BotCommand (command ="start",description ='Главное меню'),
        BotCommand (command ="help",description ='Подробная справка'),
        BotCommand (command ="wizard",description ='Пошаговая настройка'),
        BotCommand (command ="cancel",description ='Сбросить сценарий'),
        BotCommand (command ="accounts",description ='Список аккаунтов'),
        BotCommand (command ="audit_accounts",description ='Проверить аккаунты'),
        BotCommand (command ="character_info",description ="РРЅС„Рѕ Рѕ РїРµСЂСЃРѕРЅР°Р¶Рµ"),
        BotCommand (command ="chats",description ='Список привязок'),
        BotCommand (command ="binding_settings",description ='Настройки привязки'),
        BotCommand (command ="set_binding_interval",description ="РРЅС‚РµСЂРІР°Р» РїСЂРёРІСЏР·РєРё"),
        BotCommand (command ="set_binding_reply_interval",description ="РРЅС‚РµСЂРІР°Р» РѕС‚РІРµС‚РѕРІ"),
        BotCommand (command ="set_binding_context",description ='Глубина контекста'),
        BotCommand (command ="set_binding_prompt",description ='Промпт привязки'),
        BotCommand (command ="reset_binding_prompt",description ='Сбросить промпт'),
        BotCommand (command ="send_status",description ='Статус отправки'),
        BotCommand (command ="restart_runners",description ='Перезапустить задачи'),
        BotCommand (command ="delete_binding",description ='Удалить привязку'),
        ]
        )

    @dp .startup ()
    async def on_startup ()->None :
        await set_bot_commands ()

    async def send_main_menu (target :Message |CallbackQuery ,text :str ='Главное меню')->None :
        if isinstance (target ,CallbackQuery ):
            await target .message .answer (text ,reply_markup =main_menu_keyboard ())
            await target .answer ()
            return 
        await target .answer (text ,reply_markup =main_menu_keyboard ())

    async def send_accounts_list (target :Message |CallbackQuery ,notice :str |None =None )->None :
        async with SessionLocal ()as session :
            service =AccountService (session )
            accounts =await service .list_accounts_for_menu ()
            text ="Список аккаунтов:"if accounts else "Аккаунтов нет."
            if notice :
                text =f"{notice }\n\n{text }"
            kb =accounts_list_keyboard (accounts )if accounts else main_menu_keyboard ()

            if isinstance (target ,CallbackQuery ):
                await target .message .answer (text ,reply_markup =kb )
                await target .answer ()
                return 
            await target .answer (text ,reply_markup =kb )

    async def send_account_details (target :Message |CallbackQuery ,account_id :int ,notice :str |None =None )->None :
        async with SessionLocal ()as session :
            service =AccountService (session )
            account =await service .get_account (account_id )
            if account is None :
                text ="Аккаунт не найден."
                kb =main_menu_keyboard ()
            else :
                account =await service .ensure_account_name (account )
                text =format_account_details (account )
                if notice :
                    text =f"{notice }\n\n{text }"
                kb =account_actions_keyboard (account .id )

            if isinstance (target ,CallbackQuery ):
                await target .message .answer (text ,reply_markup =kb )
                await target .answer ()
                return 
            await target .answer (text ,reply_markup =kb )

    @dp .message (CommandStart ())
    async def start_handler (message :Message ,state :FSMContext )->None :
        await state .clear ()
        await send_main_menu (message ,"Р“Р»Р°РІРЅРѕРµ РјРµРЅСЋ. РСЃРїРѕР»СЊР·СѓР№ РєРЅРѕРїРєРё РЅРёР¶Рµ РёР»Рё РєРѕРјР°РЅРґСѓ /wizard.")

    @dp .message (Command ("help"))
    async def help_handler (message :Message )->None :
        await message .answer (format_help (),reply_markup =main_menu_keyboard (),parse_mode =None )

    @dp .message (Command ("cancel"))
    async def cancel_handler (message :Message ,state :FSMContext )->None :
        await state .clear ()
        await send_main_menu (message ,'Текущий сценарий сброшен.')

    @dp .message (Command ("wizard"))
    async def wizard_handler (message :Message ,state :FSMContext )->None :
        await state .clear ()
        await state .set_state (WizardStates .waiting_phone )
        await message .answer ('Введи номер телефона в формате +15550000001',reply_markup =back_keyboard ())

    @dp .message (Command ("accounts"))
    async def accounts_handler (message :Message )->None :
        await send_accounts_list (message )
        return 
        async with SessionLocal ()as session :
            accounts =await AccountService (session ).list_accounts ()
            if not accounts :
                text ='Аккаунтов нет.'
            else :
                lines =[]
                for acc in accounts :
                    char_name =acc .character .name if acc .character else 'не назначен'
                    lines .append (
                    f"ID: {acc .id } | {acc .phone }\n"
                    f"Статус: {acc .auth_status } | Активен: {int (acc .is_active )}\n"
                    f"Персонаж: {char_name }\n"
                    )
                text ="\n".join (lines )
            await message .answer (text ,reply_markup =main_menu_keyboard ())

    async def send_bindings_list (target :Message |CallbackQuery )->None :
        async with SessionLocal ()as session :
            bindings =await BindingService (session ).list_bindings ()
            text ='Список привязок (чатов):'if bindings else 'Привязок нет.'
            kb =bindings_list_keyboard (bindings )if bindings else main_menu_keyboard ()

            if isinstance (target ,CallbackQuery ):
                await target .message .answer (text ,reply_markup =kb )
                await target .answer ()
            else :
                await target .answer (text ,reply_markup =kb )

    @dp .message (Command ("chats"))
    async def chats_handler (message :Message )->None :
        await send_bindings_list (message )


    @dp .message (Command ("binding_settings"))
    async def binding_settings_handler (message :Message )->None :
        parts =(message .text or "").split ()
        if len (parts )!=2 or not parts [1 ].isdigit ():
            await message .answer ("Format: /binding_settings <binding_id>",reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).get_binding (int (parts [1 ]))
                await message .answer (format_binding_settings (binding ),reply_markup =binding_actions_keyboard (binding .id ))
            except ValueError as exc :
                await message .answer (f"Error: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("set_binding_interval"))
    async def set_binding_interval_handler (message :Message )->None :
        parts =(message .text or "").split ()
        if len (parts )not in {3 ,4 }or not parts [1 ].isdigit ()or not parts [2 ].isdigit ()or (len (parts )==4 and not parts [3 ].isdigit ()):
            await message .answer ("Format: /set_binding_interval <binding_id> <min_minutes> [max_minutes]",reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =int (parts [1 ]),
                interval_min_minutes =int (parts [2 ]),
                interval_max_minutes =int (parts [3 ])if len (parts )==4 else int (parts [2 ]),
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_actions_keyboard (binding .id ))
            except ValueError as exc :
                await message .answer (f"Error: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("set_binding_reply_interval"))
    async def set_binding_reply_interval_handler (message :Message )->None :
        parts =(message .text or "").split ()
        if len (parts )not in {3 ,4 }or not parts [1 ].isdigit ():
            await message .answer (
            'Формат: /set_binding_reply_interval <binding_id> <min_minutes> [max_minutes] | off',
            reply_markup =back_keyboard (),
            parse_mode =None ,
            )
            return 

        reply_text =" ".join (parts [2 :])
        try :
            interval_min ,interval_max ,reset_reply_interval =_parse_reply_interval_input (reply_text )
        except ValueError :
            await message .answer (
            'Формат: /set_binding_reply_interval <binding_id> <min_minutes> [max_minutes] | off',
            reply_markup =back_keyboard (),
            parse_mode =None ,
            )
            return 

        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =int (parts [1 ]),
                reply_interval_min_minutes =interval_min ,
                reply_interval_max_minutes =interval_max ,
                reset_reply_interval =reset_reply_interval ,
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_actions_keyboard (binding .id ))
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("set_binding_context"))
    async def set_binding_context_handler (message :Message )->None :
        parts =(message .text or "").split ()
        if len (parts )!=3 or not parts [1 ].isdigit ()or not parts [2 ].isdigit ():
            await message .answer ("Format: /set_binding_context <binding_id> <message_count>",reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =int (parts [1 ]),
                context_message_count =int (parts [2 ]),
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_actions_keyboard (binding .id ))
            except ValueError as exc :
                await message .answer (f"Error: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("set_binding_prompt"))
    async def set_binding_prompt_handler (message :Message )->None :
        parts =(message .text or "").split (maxsplit =2 )
        if len (parts )<3 or not parts [1 ].isdigit ():
            await message .answer ("Format: /set_binding_prompt <binding_id> <text>",reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =int (parts [1 ]),
                system_prompt =parts [2 ],
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_actions_keyboard (binding .id ))
            except ValueError as exc :
                await message .answer (f"Error: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("reset_binding_prompt"))
    async def reset_binding_prompt_handler (message :Message )->None :
        parts =(message .text or "").split ()
        if len (parts )!=2 or not parts [1 ].isdigit ():
            await message .answer ("Format: /reset_binding_prompt <binding_id>",reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =int (parts [1 ]),
                reset_prompt =True ,
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_actions_keyboard (binding .id ))
            except ValueError as exc :
                await message .answer (f"Error: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("send_status"))
    async def send_status_handler (message :Message )->None :
        async with SessionLocal ()as session :
            items =await BindingService (session ).list_binding_statuses ()
            await message .answer (format_send_status (items ),reply_markup =main_menu_keyboard ())

    @dp .message (Command ("restart_runners"))
    async def restart_runners_handler (message :Message )->None :
        try :
            restart_scheduler ()
            await message .answer ('Планировщик и задачи успешно перезапущены.',reply_markup =main_menu_keyboard ())
        except Exception as exc :
            await message .answer (f"Ошибка при рестарте: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("audit_accounts"))
    async def audit_handler (message :Message )->None :
        async with SessionLocal ()as session :
            report =await AccountService (session ).audit_accounts ()
            await message .answer (format_audit_report (report ),reply_markup =main_menu_keyboard ())

    @dp .message (Command ("character_info"))
    async def character_info_handler (message :Message )->None :
        parts =(message .text or "").split ()
        if len (parts )!=2 or not parts [1 ].isdigit ():
            await message .answer ('Формат: /character_info <account_id>',reply_markup =back_keyboard ())
            return 

        async with SessionLocal ()as session :
            account =await AccountService (session ).get_account (int (parts [1 ]))
            if not account :
                await message .answer ('Аккаунт не найден.',reply_markup =back_keyboard ())
                return 

            char =account .character 
            if not char :
                await message .answer ('Этому аккаунту не назначен персонаж.',reply_markup =back_keyboard ())
                return 

            text =(
            f"👤 Персонаж: {char .name }\n"
            f"🎂 Возраст: {char .age or '?'}\n"
            f"💼 Занятие: {char .occupation or '-'}\n"
            f"📍 Локация: {char .location or '-'}\n\n"
            f"🎭 Характер: {char .personality or '-'}\n\n"
            f"❤️ Любит: {char .likes or '-'}\n"
            f"💔 Не любит: {char .dislikes or '-'}\n\n"
            f"🗣 Стиль речи: {char .speech_style or '-'}\n\n"
            f"в„№пёЏ РСЃС‚РѕСЂРёСЏ: {char .background or '-'}"
            )
            await message .answer (text ,reply_markup =main_menu_keyboard ())

    @dp .message (Command ("add_account"))
    async def add_account_handler (message :Message )->None :
        parts =(message .text or "").split (maxsplit =2 )
        if len (parts )<2 :
            await message .answer ('Формат: /add_account <phone> [proxy_url]',reply_markup =back_keyboard (),parse_mode =None )
            return 
        phone =parts [1 ].strip ()
        proxy_url =parts [2 ].strip ()if len (parts )>2 else None 
        async with SessionLocal ()as session :
            account =await AccountService (session ).create_account (phone =phone ,proxy_url =proxy_url )
            await message .answer (
            f"Аккаунт создан: id={account .id } phone={account .phone }",
            reply_markup =main_menu_keyboard (),
            )

    @dp .message (Command ("login_code"))
    async def login_code_handler (message :Message )->None :
        parts =(message .text or "").split ()
        if len (parts )!=2 or not parts [1 ].isdigit ():
            await message .answer ('Формат: /login_code <account_id>',reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                status =await AccountService (session ).request_login_code (int (parts [1 ]))
                await message .answer (f"Статус авторизации: {status }",reply_markup =main_menu_keyboard ())
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("login_finish"))
    async def login_finish_handler (message :Message )->None :
        parts =(message .text or "").split (maxsplit =3 )
        if len (parts )<3 or not parts [1 ].isdigit ():
            await message .answer ('Формат: /login_finish <account_id> <code> [password]',reply_markup =back_keyboard (),parse_mode =None )
            return 
        account_id =int (parts [1 ])
        code =parts [2 ]
        password =parts [3 ]if len (parts )>3 else None 
        async with SessionLocal ()as session :
            try :
                status =await AccountService (session ).complete_login (account_id =account_id ,code =code ,password =password )
                await message .answer (f"Статус авторизации: {status }",reply_markup =main_menu_keyboard ())
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("login_password"))
    async def login_password_handler (message :Message )->None :
        parts =(message .text or "").split (maxsplit =2 )
        if len (parts )<3 or not parts [1 ].isdigit ():
            await message .answer ('Формат: /login_password <account_id> <your_2fa_password>',reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                status =await AccountService (session ).complete_password_login (account_id =int (parts [1 ]),password =parts [2 ])
                await message .answer (f"Статус авторизации: {status }",reply_markup =main_menu_keyboard ())
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("bind_chat"))
    async def bind_chat_handler (message :Message )->None :
        parts =(message .text or "").split (maxsplit =3 )
        if len (parts )<3 or not parts [1 ].isdigit ():
            await message .answer ('Формат: /bind_chat <account_id> <chat_id_or_username> [interval_minutes]',reply_markup =back_keyboard (),parse_mode =None )
            return 
        account_id =int (parts [1 ])
        chat_ref =parts [2 ].strip ()
        interval =int (parts [3 ])if len (parts )>3 and parts [3 ].isdigit ()else settings .default_post_interval_minutes 
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).create_binding (account_id =account_id ,chat_ref =chat_ref ,interval_minutes =interval )
                await message .answer (
                f"Привязка создана: id={binding .id } account={binding .account_id } chat={binding .chat_ref }",
                reply_markup =main_menu_keyboard (),
                )
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("delete_binding"))
    async def delete_binding_handler (message :Message )->None :
        parts =(message .text or "").split ()
        if len (parts )!=2 or not parts [1 ].isdigit ():
            await message .answer ('Формат: /delete_binding <binding_id>',reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                await BindingService (session ).delete_binding (int (parts [1 ]))
                await message .answer (f"Привязка {parts [1 ]} удалена.",reply_markup =main_menu_keyboard ())
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("generate_once"))
    async def generate_once_handler (message :Message )->None :
        parts =(message .text or "").split (maxsplit =2 )
        if len (parts )<3 or not parts [1 ].isdigit ():
            await message .answer ('Формат: /generate_once <account_id> <chat_id_or_username>',reply_markup =back_keyboard (),parse_mode =None )
            return 
        async with SessionLocal ()as session :
            try :
                content =await ChatAutomationService (session ).force_generate_and_send (account_id =int (parts [1 ]),chat_ref =parts [2 ].strip ())
                await message .answer (content ,reply_markup =main_menu_keyboard ())
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (Command ("create_group"))
    async def create_group_handler (message :Message )->None :
        raw =(message .text or "").removeprefix ("/create_group").strip ()
        if not raw :
            await message .answer ('Формат: /create_group <account_id> <описание группы>',reply_markup =back_keyboard (),parse_mode =None )
            return 
        first_space =raw .find (" ")
        if first_space ==-1 :
            await message .answer ('Формат: /create_group <account_id> <описание группы>',reply_markup =back_keyboard (),parse_mode =None )
            return 
        account_raw =raw [:first_space ].strip ()
        if not account_raw .isdigit ():
            await message .answer ('account_id должен быть числом.',reply_markup =back_keyboard ())
            return 

        description =raw [first_space +1 :].strip ()
        if not description :
            await message .answer ('Нужно описание группы.',reply_markup =back_keyboard ())
            return 

        await message .answer ('Генерирую данные для группы...',reply_markup =back_keyboard ())
        async with SessionLocal ()as session :
            try :
                chat_ref =await ChatAutomationService (session ).create_group (
                account_id =int (account_raw ),
                description =description 
                )
                await message .answer (f"Группа создана: {chat_ref } и в неё отправлены сообщения.",reply_markup =main_menu_keyboard ())
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .callback_query (F .data =="menu:wizard")
    async def callback_wizard (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        await state .set_state (WizardStates .waiting_phone )
        await callback .message .answer ('Введи номер телефона в формате +15550000001',reply_markup =back_keyboard ())
        await callback .answer ()

    @dp .callback_query (F .data =="menu:help")
    async def callback_help (callback :CallbackQuery )->None :
        await callback .message .answer (format_help (),reply_markup =main_menu_keyboard (),parse_mode =None )
        await callback .answer ()

    @dp .callback_query (F .data =="menu:accounts")
    async def callback_accounts (callback :CallbackQuery )->None :
        await send_accounts_list (callback )
        return 
        async with SessionLocal ()as session :
            accounts =await AccountService (session ).list_accounts ()
            if not accounts :
                text ='Аккаунтов нет.'
            else :
                lines =[]
                for acc in accounts :
                    char_name =acc .character .name if acc .character else 'не назначен'
                    lines .append (
                    f"ID: {acc .id } | {acc .phone }\n"
                    f"Статус: {acc .auth_status } | Активен: {int (acc .is_active )}\n"
                    f"Персонаж: {char_name }\n"
                    )
                text ="\n".join (lines )
            await callback .message .answer (text ,reply_markup =main_menu_keyboard ())
            await callback .answer ()

    @dp .callback_query (F .data .startswith ("account:view:"))
    async def callback_account_view (callback :CallbackQuery )->None :
        account_id =int ((callback .data or "").split (":")[-1 ])
        await send_account_details (callback ,account_id )

    @dp .callback_query (F .data .startswith ("account:check:"))
    async def callback_account_check (callback :CallbackQuery )->None :
        account_id =int ((callback .data or "").split (":")[-1 ])
        async with SessionLocal ()as session :
            try :
                result =await AccountService (session ).check_account (account_id )
            except ValueError as exc :
                await callback .message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
                await callback .answer ()
                return 

        notice =(
        f"Проверка: {result ['auth_status']} | "
        f"active={int (bool (result ['is_active']))} | "
        f"paused={result ['paused_bindings']} | resumed={result ['resumed_bindings']} | "
        f"{result ['reason']}"
        )
        await send_account_details (callback ,account_id ,notice =notice )

    @dp .callback_query (F .data .startswith ("account:delete:"))
    async def callback_account_delete (callback :CallbackQuery )->None :
        account_id =int ((callback .data or "").split (":")[-1 ])
        async with SessionLocal ()as session :
            try :
                result =await AccountService (session ).delete_account (account_id )
            except ValueError as exc :
                await callback .message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
                await callback .answer ()
                return 

        notice =(
        f"Аккаунт {result ['account_id']} удален. "
        f"Удалено привязок: {result ['deleted_bindings']}"
        )
        await send_accounts_list (callback ,notice =notice )

    @dp .callback_query (F .data =="menu:chats")
    async def callback_chats (callback :CallbackQuery )->None :
        await send_bindings_list (callback )

    @dp .callback_query (F .data =="menu:audit")
    async def callback_audit (callback :CallbackQuery )->None :
        async with SessionLocal ()as session :
            report =await AccountService (session ).audit_accounts ()
            await callback .message .answer (format_audit_report (report ),reply_markup =main_menu_keyboard ())
            await callback .answer ()

    @dp .callback_query (F .data =="menu:status")
    async def callback_status (callback :CallbackQuery )->None :
        async with SessionLocal ()as session :
            items =await BindingService (session ).list_binding_statuses ()
            await callback .message .answer (format_send_status (items ),reply_markup =main_menu_keyboard ())
            await callback .answer ()

    @dp .callback_query (F .data =="menu:restart_runners")
    async def callback_restart_runners (callback :CallbackQuery )->None :
        try :
            restart_scheduler ()
            await callback .message .answer ('Планировщик и задачи успешно перезапущены.',reply_markup =main_menu_keyboard ())
            await callback .answer ('Перезапущено')
        except Exception as exc :
            await callback .message .answer (f"Ошибка при рестарте: {exc }",reply_markup =back_keyboard ())
            await callback .answer ()

    @dp .callback_query (F .data =="menu:main_prompt")
    async def callback_main_prompt (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        async with SessionLocal ()as session :
            service =AppSettingsService (session )
            custom_prompt =await service .get_main_system_prompt ()
            effective_prompt =await service .get_effective_main_system_prompt ()
            await callback .message .answer (
            format_main_prompt_settings (effective_prompt ,is_custom =custom_prompt is not None ),
            reply_markup =main_prompt_keyboard (),
            parse_mode =None ,
            )
        await callback .answer ()

    @dp .callback_query (F .data =="menu:main_prompt:set")
    async def callback_main_prompt_set (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        await state .set_state (MainSettingsStates .waiting_prompt )
        await callback .message .answer (
        'Введи новый основной системный промпт. Он будет применяться ко всем чатам.',
        reply_markup =back_keyboard (),
        parse_mode =None ,
        )
        await callback .answer ()

    @dp .callback_query (F .data =="menu:main_prompt:reset")
    async def callback_main_prompt_reset (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        async with SessionLocal ()as session :
            service =AppSettingsService (session )
            await service .reset_main_system_prompt ()
            effective_prompt =await service .get_effective_main_system_prompt ()
            await callback .message .answer (
            format_main_prompt_settings (effective_prompt ,is_custom =False ),
            reply_markup =main_prompt_keyboard (),
            parse_mode =None ,
            )
        await callback .answer ()

    @dp .callback_query (F .data =="menu:model")
    async def callback_model (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        async with SessionLocal ()as session :
            service =AppSettingsService (session )
            custom_model =await service .get_openai_model ()
            effective_model =await service .get_effective_openai_model ()
            await callback .message .answer (
            format_model_settings (effective_model ,is_custom =custom_model is not None ),
            reply_markup =model_keyboard (),
            )
        await callback .answer ()

    @dp .callback_query (F .data =="menu:model:set")
    async def callback_model_set (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        await state .set_state (MainSettingsStates .waiting_model )
        await callback .message .answer (
        'Введи название модели, например `gpt-5-mini` или `claude-3-5-haiku-latest`.',
        reply_markup =back_keyboard (),
        parse_mode =None ,
        )
        await callback .answer ()

    @dp .callback_query (F .data =="menu:model:reset")
    async def callback_model_reset (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        async with SessionLocal ()as session :
            service =AppSettingsService (session )
            await service .reset_openai_model ()
            effective_model =await service .get_effective_openai_model ()
            await callback .message .answer (
            format_model_settings (effective_model ,is_custom =False ),
            reply_markup =model_keyboard (),
            )
        await callback .answer ()

    @dp .callback_query (F .data =="menu:back")
    async def callback_back (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        await send_main_menu (callback ,'Главное меню')

    @dp .callback_query (F .data .startswith ("binding:delete:"))
    async def callback_delete_binding (callback :CallbackQuery )->None :
        binding_id_raw =(callback .data or "").split (":")[-1 ]
        if not binding_id_raw .isdigit ():
            await callback .message .answer ('Ошибка: некорректный id привязки.',reply_markup =back_keyboard ())
            await callback .answer ()
            return 
        async with SessionLocal ()as session :
            try :
                binding_id =int (binding_id_raw )
                await BindingService (session ).delete_binding (binding_id )
                await callback .message .answer (f"Привязка {binding_id } удалена.",reply_markup =main_menu_keyboard ())
                await callback .answer ('Удалено')
            except ValueError as exc :
                await callback .message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
                await callback .answer ()

    @dp .callback_query (F .data =="menu:bind_chat_start")
    async def callback_bind_chat_start (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        async with SessionLocal ()as session :
            accounts =await AccountService (session ).list_accounts ()
            if not accounts :
                await callback .message .answer ('Сначала добавь аккаунт.',reply_markup =main_menu_keyboard ())
                await callback .answer ()
                return 

            builder =InlineKeyboardBuilder ()
            for acc in accounts :
                builder .button (text =f"Акк {acc .id }: {acc .phone }",callback_data =f"bind_chat_acc:{acc .id }")
            builder .button (text ='Вернуться',callback_data ="menu:back")
            builder .adjust (1 )

            await callback .message .answer ('Выбери аккаунт для привязки:',reply_markup =builder .as_markup ())
        await callback .answer ()

    @dp .callback_query (F .data .startswith ("bind_chat_acc:"))
    async def callback_bind_chat_acc (callback :CallbackQuery ,state :FSMContext )->None :
        account_id =int ((callback .data or "").split (":")[-1 ])
        await state .update_data (bind_account_id =account_id )
        await state .set_state (BindChatStates .waiting_chat_ref )
        await callback .message .answer ('Введи chat_ref (юзернейм или ID чата):',reply_markup =back_keyboard ())
        await callback .answer ()

    @dp .message (BindChatStates .waiting_chat_ref )
    async def process_bind_chat_ref (message :Message ,state :FSMContext )->None :
        chat_ref =(message .text or "").strip ()
        await state .update_data (bind_chat_ref =chat_ref )
        await state .set_state (BindChatStates .waiting_interval )
        await message .answer ("Введи интервал в минутах (одно число или от-до, например '10 15') или 'skip' для дефолта:",reply_markup =back_keyboard ())

    @dp .message (BindChatStates .waiting_interval )
    async def process_bind_chat_interval (message :Message ,state :FSMContext )->None :
        text =(message .text or "").strip ()
        data =await state .get_data ()
        account_id =data .get ("bind_account_id")
        chat_ref =data .get ("bind_chat_ref")

        interval_min =settings .default_post_interval_minutes 
        interval_max =settings .default_post_interval_minutes 
        if text .lower ()!="skip":
            parts =text .split ()
            if not all (p .isdigit ()for p in parts )or len (parts )>2 :
                await message .answer ("Ошибка. Введи 'skip' или числа (например '5' или '5 15'):",reply_markup =back_keyboard ())
                return 
            interval_min =int (parts [0 ])
            interval_max =int (parts [1 ])if len (parts )>1 else interval_min 

        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).create_binding (
                account_id =account_id ,
                chat_ref =chat_ref ,
                interval_minutes =interval_min 
                )
                if interval_max !=interval_min :
                    binding =await BindingService (session ).update_binding_settings (
                    binding_id =binding .id ,
                    interval_min_minutes =interval_min ,
                    interval_max_minutes =interval_max 
                    )
                await message .answer (f"Привязка создана.\nОна будет отображаться в списке чатов.",reply_markup =binding_settings_keyboard (binding .id ))
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
        await state .clear ()

    @dp .callback_query (F .data .startswith ("binding:settings:"))
    async def callback_binding_settings (callback :CallbackQuery ,state :FSMContext )->None :
        await state .clear ()
        binding_id =int ((callback .data or "").split (":")[-1 ])
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).get_binding (binding_id )
                await callback .message .answer (format_binding_settings (binding ),reply_markup =binding_settings_keyboard (binding_id ))
            except ValueError as exc :
                await callback .message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
        await callback .answer ()

    @dp .callback_query (F .data .startswith ("binding:send_once:"))
    async def callback_binding_send_once (callback :CallbackQuery )->None :
        binding_id =int ((callback .data or "").split (":")[-1 ])
        async with SessionLocal ()as session :
            try :
                binding_service =BindingService (session )
                automation_service =ChatAutomationService (session )
                binding =await binding_service .get_binding (binding_id )
                content =await automation_service .force_generate_and_send_binding (binding )
                refreshed_binding =await binding_service .get_binding (binding_id )
                notice =f"Сообщение отправлено:\n\n{content }"if content else "Не удалось сгенерировать сообщение."
                await callback .message .answer (
                f"{notice }\n\n{format_binding_settings (refreshed_binding )}",
                reply_markup =binding_settings_keyboard (binding_id ),
                parse_mode =None ,
                )
            except ValueError as exc :
                await callback .message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard (),parse_mode =None )
        await callback .answer ()

    @dp .callback_query (F .data .startswith ("binding:set_prompt:"))
    async def callback_binding_set_prompt (callback :CallbackQuery ,state :FSMContext )->None :
        binding_id =int ((callback .data or "").split (":")[-1 ])
        await state .update_data (binding_id =binding_id )
        await state .set_state (BindingSettingsStates .waiting_prompt )
        await callback .message .answer (f"Введи новый системный промпт для привязки {binding_id }:",reply_markup =back_keyboard ())
        await callback .answer ()

    @dp .message (BindingSettingsStates .waiting_prompt )
    async def process_waiting_prompt (message :Message ,state :FSMContext )->None :
        data =await state .get_data ()
        binding_id =data .get ("binding_id")
        prompt =(message .text or "").strip ()
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =binding_id ,
                system_prompt =prompt ,
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_settings_keyboard (binding_id ))
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
        await state .clear ()

    @dp .message (MainSettingsStates .waiting_prompt )
    async def process_waiting_main_prompt (message :Message ,state :FSMContext )->None :
        prompt =(message .text or "").strip ()
        async with SessionLocal ()as session :
            service =AppSettingsService (session )
            try :
                saved_prompt =await service .set_main_system_prompt (prompt )
                await message .answer (
                format_main_prompt_settings (saved_prompt ,is_custom =True ),
                reply_markup =main_prompt_keyboard (),
                parse_mode =None ,
                )
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard (),parse_mode =None )
                return 
        await state .clear ()

    @dp .message (MainSettingsStates .waiting_model )
    async def process_waiting_model (message :Message ,state :FSMContext )->None :
        model =(message .text or "").strip ()
        async with SessionLocal ()as session :
            service =AppSettingsService (session )
            try :
                saved_model =await service .set_openai_model (model )
                await message .answer (
                format_model_settings (saved_model ,is_custom =True ),
                reply_markup =model_keyboard (),
                )
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard (),parse_mode =None )
                return 
        await state .clear ()

    @dp .callback_query (F .data .startswith ("binding:set_interval:"))
    async def callback_binding_set_interval (callback :CallbackQuery ,state :FSMContext )->None :
        binding_id =int ((callback .data or "").split (":")[-1 ])
        await state .update_data (binding_id =binding_id )
        await state .set_state (BindingSettingsStates .waiting_interval )
        await callback .message .answer (f"Введи интервал для привязки {binding_id } (например, '5' или '5 15' для случайного):",reply_markup =back_keyboard ())
        await callback .answer ()

    @dp .message (BindingSettingsStates .waiting_interval )
    async def process_waiting_interval (message :Message ,state :FSMContext )->None :
        data =await state .get_data ()
        binding_id =data .get ("binding_id")
        parts =(message .text or "").split ()
        if not parts or not all (p .isdigit ()for p in parts )or len (parts )>2 :
            await message .answer ("Ошибка формата. Введи одно или два числа (например '5' или '5 15').",reply_markup =back_keyboard ())
            return 

        interval_min =int (parts [0 ])
        interval_max =int (parts [1 ])if len (parts )>1 else interval_min 

        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =binding_id ,
                interval_min_minutes =interval_min ,
                interval_max_minutes =interval_max ,
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_settings_keyboard (binding_id ))
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
        await state .clear ()

    @dp .callback_query (F .data .startswith ("binding:set_reply_interval:"))
    async def callback_binding_set_reply_interval (callback :CallbackQuery ,state :FSMContext )->None :
        binding_id =int ((callback .data or "").split (":")[-1 ])
        await state .update_data (binding_id =binding_id )
        await state .set_state (BindingSettingsStates .waiting_reply_interval )
        await callback .message .answer (
        f"Введи интервал ответов для привязки {binding_id } (`15`, `15 30` или `off`):",
        reply_markup =back_keyboard (),
        parse_mode =None ,
        )
        await callback .answer ()

    @dp .message (BindingSettingsStates .waiting_reply_interval )
    async def process_waiting_reply_interval (message :Message ,state :FSMContext )->None :
        data =await state .get_data ()
        binding_id =data .get ("binding_id")
        try :
            interval_min ,interval_max ,reset_reply_interval =_parse_reply_interval_input ((message .text or "").strip ())
        except ValueError :
            await message .answer (
            'Ошибка формата. Введи `15`, `15 30` или `off`.',
            reply_markup =back_keyboard (),
            parse_mode =None ,
            )
            return 

        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =binding_id ,
                reply_interval_min_minutes =interval_min ,
                reply_interval_max_minutes =interval_max ,
                reset_reply_interval =reset_reply_interval ,
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_settings_keyboard (binding_id ))
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
        await state .clear ()

    @dp .callback_query (F .data .startswith ("binding:set_context:"))
    async def callback_binding_set_context (callback :CallbackQuery ,state :FSMContext )->None :
        binding_id =int ((callback .data or "").split (":")[-1 ])
        await state .update_data (binding_id =binding_id )
        await state .set_state (BindingSettingsStates .waiting_context )
        await callback .message .answer (f"Введи количество сообщений для парсинга (контекст) для привязки {binding_id }:",reply_markup =back_keyboard ())
        await callback .answer ()

    @dp .message (BindingSettingsStates .waiting_context )
    async def process_waiting_context (message :Message ,state :FSMContext )->None :
        data =await state .get_data ()
        binding_id =data .get ("binding_id")
        text =(message .text or "").strip ()
        if not text .isdigit ():
            await message .answer ('Должно быть число.',reply_markup =back_keyboard ())
            return 

        count =int (text )
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).update_binding_settings (
                binding_id =binding_id ,
                context_message_count =count ,
                )
                await message .answer (format_binding_settings (binding ),reply_markup =binding_settings_keyboard (binding_id ))
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())
        await state .clear ()

    @dp .message (WizardStates .waiting_phone )
    async def wizard_phone (message :Message ,state :FSMContext )->None :
        phone =(message .text or "").strip ()
        if not phone .startswith ("+"):
            await message .answer ('Телефон должен начинаться с +',reply_markup =back_keyboard ())
            return 
        await state .update_data (phone =phone )
        await state .set_state (WizardStates .waiting_proxy )
        await message .answer ('Введи proxy URL или skip',reply_markup =back_keyboard ())

    @dp .message (WizardStates .waiting_proxy )
    async def wizard_proxy (message :Message ,state :FSMContext )->None :
        proxy_text =(message .text or "").strip ()
        proxy_url =None if proxy_text .lower ()=="skip"else proxy_text 
        data =await state .get_data ()
        async with SessionLocal ()as session :
            try :
                account =await AccountService (session ).create_account (phone =data ["phone"],proxy_url =proxy_url )
                await state .update_data (account_id =account .id ,proxy_url =proxy_url )
                status =await AccountService (session ).request_login_code (account .id )
                await state .set_state (WizardStates .waiting_code )
                await message .answer (
                f"Статус авторизации: {status }\nТеперь введи код из Telegram.",
                reply_markup =back_keyboard (),
                )
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (WizardStates .waiting_code )
    async def wizard_code (message :Message ,state :FSMContext )->None :
        code =(message .text or "").strip ()
        data =await state .get_data ()
        async with SessionLocal ()as session :
            try :
                status =await AccountService (session ).complete_login (account_id =int (data ["account_id"]),code =code )
                if status =="password_required":
                    await state .set_state (WizardStates .waiting_password )
                    await message .answer ('Нужен пароль 2FA. Введи пароль.',reply_markup =back_keyboard ())
                    return 
                await state .set_state (WizardStates .waiting_chat_ref )
                await message .answer (f"Статус авторизации: {status }\nВведи chat_ref или skip.",reply_markup =back_keyboard ())
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (WizardStates .waiting_password )
    async def wizard_password (message :Message ,state :FSMContext )->None :
        password =(message .text or "").strip ()
        data =await state .get_data ()
        async with SessionLocal ()as session :
            try :
                status =await AccountService (session ).complete_password_login (account_id =int (data ["account_id"]),password =password )
                await state .set_state (WizardStates .waiting_chat_ref )
                await message .answer (f"Статус авторизации: {status }\nВведи chat_ref или skip.",reply_markup =back_keyboard ())
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message (WizardStates .waiting_chat_ref )
    async def wizard_chat_ref (message :Message ,state :FSMContext )->None :
        chat_ref =(message .text or "").strip ()
        if chat_ref .lower ()=="skip":
            await state .clear ()
            await send_main_menu (message ,'Мастер завершен без создания привязки.')
            return 
        await state .update_data (chat_ref =chat_ref )
        await state .set_state (WizardStates .waiting_interval )
        await message .answer ('Введи интервал в минутах или skip.',reply_markup =back_keyboard ())

    @dp .message (WizardStates .waiting_interval )
    async def wizard_interval (message :Message ,state :FSMContext )->None :
        interval_text =(message .text or "").strip ()
        if interval_text .lower ()=="skip":
            interval =settings .default_post_interval_minutes 
        else :
            if not interval_text .isdigit ():
                await message .answer ("РРЅС‚РµСЂРІР°Р» РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ С‡РёСЃР»РѕРј РёР»Рё skip.",reply_markup =back_keyboard ())
                return 
            interval =int (interval_text )
        data =await state .get_data ()
        async with SessionLocal ()as session :
            try :
                binding =await BindingService (session ).create_binding (
                account_id =int (data ["account_id"]),
                chat_ref =str (data ["chat_ref"]),
                interval_minutes =interval ,
                )
                await state .clear ()
                await message .answer (
                f"Мастер завершен. Привязка создана: id={binding .id } chat={binding .chat_ref }",
                reply_markup =main_menu_keyboard (),
                )
            except ValueError as exc :
                await message .answer (f"Ошибка: {exc }",reply_markup =back_keyboard ())

    @dp .message ()
    async def fallback_handler (message :Message )->None :
        if not (message .text or "").strip ():
            await message .answer ('Неподдерживаемый формат сообщения.',reply_markup =back_keyboard ())
            return 
        await message .answer ("РќРµ РїРѕРЅСЏР» СЃРѕРѕР±С‰РµРЅРёРµ. РСЃРїРѕР»СЊР·СѓР№ /start, /help РёР»Рё /wizard.",reply_markup =main_menu_keyboard ())

    @dp .error ()
    async def error_handler (event )->bool :
        update =event .update 
        exception =event .exception 
        logger .exception ("bot_handler_failed",exc_info =exception )
        message =getattr (update ,"message",None )
        callback =getattr (update ,"callback_query",None )
        text =f"Ошибка: {exception }"
        try :
            if message :
                await message .answer (text ,reply_markup =back_keyboard ())
            elif callback :
                await callback .message .answer (text ,reply_markup =back_keyboard ())
                await callback .answer ()
        except TelegramBadRequest :
            pass 
        return True 

    return bot ,dp 


async def run_bot ()->None :
    bot ,dp =build_bot ()
    await dp .start_polling (bot )


def main ()->None :
    asyncio .run (run_bot ())


if __name__ =="__main__":
    main ()





