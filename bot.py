import logging
import random
import re
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from config import ADMIN_IDS, BOT_TITLE, BOT_TOKEN, MAX_MULTI_DRAW, REWARDS
from database import init_db, upsert_user, get_user, find_user_by_username, change_tickets, perform_draws, get_ranking, get_draw_history

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

COMMAND_RE = re.compile(r"^/(?P<command>시작|도움말|등록|내뽑기권|뽑기|지급|회수|랭킹|당첨내역)(?:@[A-Za-z0-9_]+)?(?:\s+(?P<args>.*))?$")

def is_group(update):
    return bool(update.effective_chat and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP))

def register(user):
    if user and not user.is_bot:
        upsert_user(user.id, user.username, user.full_name or "이름 없음")

def uname(user):
    return f"@{escape(user.username)}" if user.username else escape(user.full_name)

def keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎰 1회 뽑기", callback_data="draw:1"), InlineKeyboardButton("🎟 내 뽑기권", callback_data="tickets")],
        [InlineKeyboardButton("🏆 랭킹", callback_data="ranking")]
    ])

def positive_int(text):
    try:
        n = int(text)
        return n if n > 0 else None
    except Exception:
        return None

async def help_cmd(update):
    text = (f"🎰 <b>{escape(BOT_TITLE)}</b>\n\n"
            "회원: /내뽑기권, /뽑기, /뽑기 10, /당첨내역, /랭킹\n\n"
            "관리자: 회원 메시지에 답장 후 /지급 5 또는 /회수 3\n"
            "개인채팅 /start는 필요하지 않습니다.")
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard())

async def tickets_cmd(update, callback=False):
    user = update.effective_user
    register(user)
    row = get_user(user.id)
    text = (f"🎟 <b>{uname(user)}님의 뽑기권</b>\n\n"
            f"보유: <b>{row['tickets']:,}장</b>\n"
            f"누적 당첨: <b>{row['total_points']:,}포인트</b>\n"
            f"총 뽑기: <b>{row['total_draws']:,}회</b>")
    if callback:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard())
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard())

def choose(count):
    names = [x[0] for x in REWARDS]
    weights = [x[2] for x in REWARDS]
    points = {x[0]:x[1] for x in REWARDS}
    return [(n, points[n]) for n in random.choices(names, weights=weights, k=count)]

async def draw_cmd(update, count, callback=False):
    user = update.effective_user
    register(user)
    if count < 1 or count > MAX_MULTI_DRAW:
        msg = f"뽑기 횟수는 1~{MAX_MULTI_DRAW}회까지만 가능합니다."
        if callback: await update.callback_query.answer(msg, show_alert=True)
        else: await update.effective_message.reply_text(msg)
        return
    rewards = choose(count)
    try:
        remaining,total = perform_draws(user.id,rewards,count)
    except Exception as e:
        if callback: await update.callback_query.answer(str(e), show_alert=True)
        else: await update.effective_message.reply_text(str(e))
        return
    max_reward = max(x[1] for x in REWARDS)
    if count == 1:
        text = (f"🎰 <b>{escape(BOT_TITLE)}</b>\n\n👤 {uname(user)}\n\n"
                f"━━━━━━━━━━━━━━\n🎁 <b>{escape(rewards[0][0])}</b>\n━━━━━━━━━━━━━━\n\n"
                f"🎟 남은 뽑기권: <b>{remaining:,}장</b>\n🏆 최대 당첨금: <b>{max_reward:,}포인트</b>")
    else:
        lines = [f"{i}. {escape(n)}" for i,(n,_) in enumerate(rewards,1)]
        text = (f"🎰 <b>{count}회 뽑기 완료!</b>\n\n" + "\n".join(lines) +
                f"\n\n━━━━━━━━━━━━━━\n💰 총 획득: <b>{total:,}포인트</b>\n"
                f"🎟 남은 뽑기권: <b>{remaining:,}장</b>\n"
                f"🏆 이번 최고 당첨: <b>{max(p for _,p in rewards):,}포인트</b>")
    if callback:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard())
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard())

async def admin_action(update, command, args):
    admin = update.effective_user
    if admin.id not in ADMIN_IDS:
        await update.effective_message.reply_text("관리자만 사용할 수 있습니다.")
        return
    msg = update.effective_message
    target = None
    amount = None
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target = msg.reply_to_message.from_user
        if target.is_bot:
            await msg.reply_text("봇 계정에는 지급할 수 없습니다.")
            return
        register(target)
        amount = positive_int(args.strip())
    else:
        parts = args.split()
        if len(parts) == 2 and parts[0].startswith("@"):
            amount = positive_int(parts[1])
            target = find_user_by_username(parts[0])
    if not target or not amount:
        await msg.reply_text("사용법: 회원 메시지에 답장하고 /지급 5")
        return
    if hasattr(target,"id"):
        target_id = target.id
        target_name = uname(target)
    else:
        target_id = int(target["user_id"])
        target_name = f"@{escape(target['username'])}" if target["username"] else escape(target["display_name"])
    try:
        before,after = change_tickets(admin.id,target_id,amount if command=="지급" else -amount,update.effective_chat.id)
    except Exception as e:
        await msg.reply_text(str(e))
        return
    await msg.reply_text(f"✅ <b>{target_name}</b>님 뽑기권 {amount:,}장 {command} 완료\n{before:,}장 → <b>{after:,}장</b>", parse_mode=ParseMode.HTML)

async def ranking_cmd(update, callback=False):
    rows = get_ranking(10)
    if not rows:
        text = "아직 뽑기 기록이 없습니다."
    else:
        medals=["🥇","🥈","🥉"]
        lines=[f"🏆 <b>{escape(BOT_TITLE)} 랭킹</b>\n"]
        for i,row in enumerate(rows,1):
            p=medals[i-1] if i<=3 else f"{i}."
            name=f"@{escape(row['username'])}" if row['username'] else escape(row['display_name'])
            lines.append(f"{p} {name} — <b>{row['total_points']:,}P</b> ({row['total_draws']:,}회)")
        text="\n".join(lines)
    if callback:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard())
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard())

async def history_cmd(update):
    user=update.effective_user
    register(user)
    rows=get_draw_history(user.id,10)
    if not rows:
        await update.effective_message.reply_text("아직 당첨 내역이 없습니다.")
        return
    lines=[f"📜 <b>{uname(user)}님의 최근 당첨 내역</b>\n"]+[f"{i}. {escape(r['reward_name'])}" for i,r in enumerate(rows,1)]
    await update.effective_message.reply_text("\n".join(lines),parse_mode=ParseMode.HTML)

async def command_router(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        await update.effective_message.reply_text("이 봇은 그룹에서만 사용할 수 있습니다.")
        return
    register(update.effective_user)
    m=COMMAND_RE.match(update.effective_message.text or "")
    if not m: return
    cmd=m.group("command"); args=(m.group("args") or "").strip()
    if cmd in ("시작","도움말","등록"): await help_cmd(update)
    elif cmd=="내뽑기권": await tickets_cmd(update)
    elif cmd=="뽑기":
        count=1 if not args else positive_int(args)
        if count is None: await update.effective_message.reply_text("사용법: /뽑기 또는 /뽑기 10")
        else: await draw_cmd(update,count)
    elif cmd in ("지급","회수"): await admin_action(update,cmd,args)
    elif cmd=="랭킹": await ranking_cmd(update)
    elif cmd=="당첨내역": await history_cmd(update)

async def callback_router(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        await update.callback_query.answer("그룹에서만 사용할 수 있습니다.",show_alert=True); return
    data=update.callback_query.data or ""
    if data=="tickets": await tickets_cmd(update,True)
    elif data=="ranking": await ranking_cmd(update,True)
    elif data.startswith("draw:"): await draw_cmd(update,int(data.split(":",1)[1]),True)

async def passive_register(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if is_group(update) and update.effective_user: register(update.effective_user)

async def error_handler(update, context):
    logger.exception("업데이트 처리 중 오류", exc_info=context.error)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Railway Variables에 BOT_TOKEN을 등록하세요.")
    if not ADMIN_IDS:
        raise RuntimeError("Railway Variables에 ADMIN_IDS를 등록하세요.")
    init_db()
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(COMMAND_RE), command_router),group=0)
    app.add_handler(CallbackQueryHandler(callback_router),group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.StatusUpdate.ALL, passive_register),group=1)
    app.add_error_handler(error_handler)
    logger.info("봇 실행 시작")
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
