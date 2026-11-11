/**
 * Arknights SSS (AutoChess) agent — frida-il2cpp-bridge.
 *
 * Game API surface (Torappu.Battle.AutoChess):
 *   AutoChessGameManager : SingletonWithMonoHost<AutoChessGameManager, BattleController>
 *     .instanceOrNull                       (static, on generic base)
 *     .dataCenter        @0x40 → AutoChessDataCenter
 *     .playerOpManager   @0x60 → AutoChessPlayerOpManager
 *     .gameStatus        @0x38 → AutoChessGameStatus (gameState)
 *   AutoChessDataCenter
 *     .get_chessMapInfo() / .playerDataModel @0xF8 / .selfBattleData
 *   ChessMapInfo.chessInstMap : ListDict<int, ChessInst>  (List<KVP>)
 *   ChessInst { instId, chessId, dir, pos{row,col}, chessType,
 *               playerIndex, side, isInHand, isInBattle }
 *   AutoChessOperationSnapshot { operationType, start{row,col},
 *                                end{row,col}, direction }  (24B)
 *   OpManager.ConstructOperation(snap) + DoOperation()
 *
 * Server protocol layer (Torappu.UI.AutoChess.Server):
 *   shop/strategy/ready actions are request packets, not OpManager ops.
 *   AutoChessService.SendRequest(AutoChessServiceRequest) — static.
 *   Request classes are nested inside AutoChessBattleProtocol /
 *   AutoChessTeamProtocol; il2cpp names are the plain nested names in
 *   namespace Torappu.UI.AutoChess.Server.
 *   posCode = (row << 16) + col   (GridPosition.Encode, disasm-verified)
 */
import "frida-il2cpp-bridge";

const NS_AC = "Torappu.Battle.AutoChess";
const NS_SRV = "Torappu.UI.AutoChess.Server";
const IMG = "Assembly-CSharp";

let ready = false;
let C: any = {};            // resolved classes
let getGameManager: () => any;
let getService: () => any;

function log(...a: any[]) { console.log("[ak]", ...a); }

function resolveClasses() {
    const img = Il2Cpp.domain.assembly(IMG).image;
    C.GameManager = img.class(`${NS_AC}.AutoChessGameManager`);
    C.DataCenter = img.class(`${NS_AC}.AutoChessDataCenter`);
    C.ChessMapInfo = img.class(`${NS_AC}.ChessMapInfo`);
    C.ChessInst = img.class(`${NS_AC}.ChessInst`);
    C.OpManager = img.class(`${NS_AC}.AutoChessPlayerOpManager`);
    C.Snapshot = img.class(`${NS_AC}.AutoChessOperationSnapshot`);
    C.GridPosition = img.class(`Torappu.GridPosition`);
    C.BattleController = img.class(`Torappu.Battle.BattleController`);

    C.Service = img.class(`${NS_SRV}.AutoChessService`);

    // instanceOrNull lives on the generic base → inflate it
    const base = img.class("Torappu.SingletonWithMonoHost`2")
        .inflate(C.GameManager, C.BattleController);
    const getter = base.method("get_instanceOrNull");
    getGameManager = () => getter.invoke() as Il2Cpp.Object;

    // AutoChessService instance lives on ServiceCoreBase<T>.status
    const svcBase = img.class("Torappu.SocketNetwork.SvrCom.ServiceCoreBase`1")
        .inflate(C.Service);
    const statusGetter = svcBase.method("get_status");
    getService = () => statusGetter.invoke() as Il2Cpp.Object;
}

function gm(): Il2Cpp.Object {
    const m = getGameManager();
    if (m.isNull()) throw new Error("AutoChessGameManager not in battle");
    return m;
}

function field(o: any, name: string) { return o.field(name).value; }
function call(o: any, name: string, ...a: any[]) {
    return o.method(name).invoke(...a);
}

// ListDict<int,ChessInst> == List<KVP<int,ChessInst>> — read _items/_size
function eachKvp(listObj: Il2Cpp.Object, cb: (k: number, v: Il2Cpp.Object) => void) {
    const items = listObj.field("_items").value as Il2Cpp.Array;
    const size = listObj.field("_size").value as number;
    for (let i = 0; i < size; i++) {
        const kvp = items.get(i) as any;      // value type → Il2Cpp.ValueType
        const v = (kvp.field ? kvp.field("value").value : call(kvp, "get_Value")) as Il2Cpp.Object;
        const k = (kvp.field ? kvp.field("key").value : call(kvp, "get_Key")) as number;
        cb(k, v);
    }
}

function s(v: any): string {
    return v == null || v.isNull?.() ? "" : (v.content ?? v.toString());
}

function readState(): any {
    const m = gm();
    const dc = field(m, "dataCenter") as Il2Cpp.Object;
    const status = field(m, "gameStatus") as Il2Cpp.Object;

    const out: any = {
        in_battle: true,
        game_state: 0,
        round: 0,
        stage_id: s(field(dc, "<stageId>k__BackingField")),
        chess: [] as any[],
    };
    // AutoChessGameStatus exposes gameCurrentStateId (getter name verified in dump)
    try { out.game_state = call(status, "get_gameCurrentStateId"); } catch (_) {
        try { out.game_state = call(status, "get_gameState"); } catch (_) { }
    }
    try { out.round = field(dc, "<currentRound>k__BackingField"); } catch (_) { }
    try {
        out.self_index = field(dc, "<selfPlayerIndex>k__BackingField");
    } catch (_) { }

    // player state
    try {
        const pdm = field(dc, "playerDataModel") as Il2Cpp.Object;
        const spd = call(pdm, "GetPlayerData", out.self_index ?? 0);
        if (spd && !spd.isNull()) {
            out.player = {
                index: field(spd, "index"),
                hp: field(spd, "hp"),
                shop_level: field(spd, "shopLevel"),
                shop_coin: field(spd, "shopCoin"),
                max_deploy: field(spd, "maxDeploymentCnt"),
                streak: field(spd, "streak"),
                game_state: field(spd, "gameState"),
            };
        }
    } catch (e) { out.player_err = String(e); }

    // board
    try {
        const cmap = call(dc, "get_chessMapInfo") as Il2Cpp.Object;
        const instMap = field(cmap, "chessInstMap") as Il2Cpp.Object;
        eachKvp(instMap, (_k, inst) => {
            if (inst.isNull()) return;
            const pos = inst.field("pos").value as any;
            const row = pos.field ? pos.field("row").value : pos.field("m_Row").value;
            const col = pos.field ? pos.field("col").value : pos.field("m_Col").value;
            let inHand = false, inBattle = false;
            try { inHand = !!call(inst, "get_isInHand"); } catch (_) { }
            try { inBattle = !!call(inst, "get_isInBattle"); } catch (_) { }
            out.chess.push({
                inst_id: field(inst, "instId"),
                chess_id: s(field(inst, "chessId")),
                row, col,
                dir: field(inst, "dir"),
                item_type: field(inst, "chessType"),
                player_index: field(inst, "playerIndex"),
                side: field(inst, "side"),
                in_hand: inHand, in_battle: inBattle,
                golden: (() => { try { return !!call(inst, "get_isGolden"); } catch (_) { return false; } })(),
            });
        });
    } catch (e) { out.chess_err = String(e); }

    // shop — dataCenter.shopDataModel @0x100 (AutoChessShopDataModel)
    try {
        const shop = field(dc, "shopDataModel") as Il2Cpp.Object;
        if (!shop.isNull()) {
            const goods: any[] = [];
            const list = field(shop, "chessGoods") as Il2Cpp.Object;
            if (list && !list.isNull()) {
                const items = list.field("_items").value as Il2Cpp.Array;
                const size = list.field("_size").value as number;
                for (let i = 0; i < size; i++) {
                    const g = items.get(i) as Il2Cpp.Object;
                    if (g.isNull()) continue;
                    goods.push({
                        chess_id: s(field(g, "chessId")),
                        slot_id: field(g, "slotId"),
                        price: field(g, "price"),
                        frozen: !!field(g, "isFrozen"),
                    });
                }
            }
            out.shop = {
                level: field(shop, "level"),
                coin: field(shop, "currentCoin"),
                upgrade_price: field(shop, "upgradePrice"),
                refresh_price: field(shop, "refreshPrice"),
                goods_type: field(shop, "goodsType"),
                free_refresh: field(shop, "freeRefreshCnt"),
                special_refresh: !!field(shop, "specialRefresh"),
                goods,
            };
        }
    } catch (e) { out.shop_err = String(e); }
    return out;
}

// ---- team-phase context (strategy pick / DIY / room) ---------------------
// AutoChessService.status → teamInfo.teamStatus (AutoChessTeamStatus struct):
//   state, teamStateEndTs, modeId, stageId, bossId, monsterKeys,
//   players[]{uid,nickName,strategy}, strategyDecisionBrief{...}
function strList(list: any): string[] {
    const out: string[] = [];
    if (!list || list.isNull?.()) return out;
    const items = list.field("_items").value as Il2Cpp.Array;
    const size = list.field("_size").value as number;
    for (let i = 0; i < size; i++) out.push(s(items.get(i)));
    return out;
}

function readTeam(): any {
    const svc = getService();
    if (svc.isNull()) return { in_team: false };
    const info = call(svc, "get_teamInfo") as Il2Cpp.Object;
    if (info.isNull()) return { in_team: false };
    const ts = info.field("teamStatus").value as any;   // struct view
    const out: any = { in_team: true };
    try { out.state = ts.field("state").value; } catch (_) { }
    try { out.state_end_ts = ts.field("teamStateEndTs").value?.toString(); } catch (_) { }
    try { out.mode_id = s(ts.field("modeId").value); } catch (_) { }
    try { out.stage_id = s(ts.field("stageId").value); } catch (_) { }
    try { out.boss_id = s(ts.field("bossId").value); } catch (_) { }
    try { out.monster_keys = strList(ts.field("monsterKeys").value); } catch (_) { }
    try {
        const players: any[] = [];
        const plist = ts.field("players").value as Il2Cpp.Object;
        if (plist && !plist.isNull()) {
            const items = plist.field("_items").value as Il2Cpp.Array;
            const size = plist.field("_size").value as number;
            for (let i = 0; i < size; i++) {
                const p = items.get(i) as Il2Cpp.Object;
                if (p.isNull()) continue;
                players.push({
                    uid: s(field(p, "uid")),
                    name: s(field(p, "nickName")),
                    strategy: s(field(p, "strategy")),
                });
            }
        }
        out.players = players;
    } catch (_) { }
    try {
        const sd = ts.field("strategyDecisionBrief").value as any;
        const choices: any[] = [];
        const clist = sd.field("choices").value as Il2Cpp.Object;
        if (clist && !clist.isNull()) {
            const items = clist.field("_items").value as Il2Cpp.Array;
            const size = clist.field("_size").value as number;
            for (let i = 0; i < size; i++) {
                const c = items.get(i) as Il2Cpp.Object;
                if (c.isNull()) continue;
                choices.push({
                    uid: s(field(c, "uid")),
                    strategy: s(field(c, "strategy")),
                    pass_cnt: field(c, "passCnt"),
                });
            }
        }
        out.strategy_brief = {
            round_fin_ts: sd.field("roundFinTs").value?.toString(),
            can_skip: !!sd.field("canSkip").value,
            curr_uid: s(sd.field("currUID").value),
            choices,
        };
    } catch (_) { }
    return out;
}

// ---- server request layer -----------------------------------------------
// sendreq(name, args) → AutoChessService.SendRequest(req). Request objects
// are plain managed classes: alloc + set fields + send.
const REQ: Record<string, { cls: string; fields?: Record<string, "int" | "bool" | "str"> }> = {
    // battle scene (AutoChessBattleProtocol.*)
    loading_ready: { cls: "AutoChessBattleSceneLoadingReadyUp" },   // needs roundEnemies — verify live
    prep_ready:    { cls: "AutoChessBattleScenePreparationReadyUp", fields: { readyType: "int" } },
    shop_refresh:  { cls: "AutoChessBattleSceneShopRefreshUp" },
    shop_buy:      { cls: "AutoChessBattleSceneShopBuyChessUp", fields: { slotId: "int", isSpecial: "bool" } },
    shop_froze:    { cls: "AutoChessBattleSceneShopFrozeUp", fields: { isFrozen: "bool" } },
    shop_upgrade:  { cls: "AutoChessBattleSceneShopUpgradeUp" },
    use_equip:     { cls: "AutoChessBattleSceneUseEquipUp", fields: { charChessInstId: "int", equipChessInstId: "int", isChangeEquip: "bool", unloadInstId: "int" } },
    destroy_chess: { cls: "AutoChessBattleSceneDestroyChessUp", fields: { chessInstId: "int", chessType: "int" } },
    sp_pick:       { cls: "AutoChessBattleSceneSpPreparationPickUp", fields: { slotId: "int" } },
    self_pick:     { cls: "AutoChessBattleSceneSelfChoicePickUp", fields: { slotId: "int" } },
    observe:       { cls: "AutoChessBattleSceneObserveUp" },
    // team scene (AutoChessTeamProtocol.*)
    choose_strategy: { cls: "AutoChessTeamChooseStrategyUp", fields: { skip: "bool", strategy: "str" } },
    team_ready:    { cls: "AutoChessTeamReadyInRoomUp", fields: { ready: "bool" } },
    start_match:   { cls: "AutoChessTeamStartMatchUp" },
    enemy_assign_ready: { cls: "AutoChessTeamEnemyAssignReadyUp", fields: { ready: "bool" } },
    diy_revoke:    { cls: "AutoChessTeamDiyChooseRevokeUp" },
};
const reqCache: Record<string, Il2Cpp.Class> = {};

function reqClass(name: string): Il2Cpp.Class {
    if (!reqCache[name]) {
        const img = Il2Cpp.domain.assembly(IMG).image;
        reqCache[name] = img.class(`${NS_SRV}.${name}`);
    }
    return reqCache[name];
}

function sendRequest(name: string, args: any): any {
    const spec = REQ[name];
    if (!spec) return { ok: false, err: `unknown request ${name}` };
    try {
        const req = reqClass(spec.cls).alloc() as Il2Cpp.Object;
        for (const [fname, ftype] of Object.entries(spec.fields ?? {})) {
            const v = args?.[fname];
            if (v === undefined) continue;
            req.field(fname).value =
                ftype === "str" ? Il2Cpp.string(String(v)) :
                ftype === "bool" ? !!v : v | 0;
        }
        // SendRequest(AutoChessServiceRequest) — pick that overload
        const m = C.Service.method("SendRequest")
            .overload(`${NS_SRV}.AutoChessServiceRequest`);
        m.invoke(req);
        return { ok: true };
    } catch (e) {
        return { ok: false, err: String(e) };
    }
}

// Build List<AutoChessBattleChessPosUnitInfo> for change_position / use_magic.
function makeUnitList(units: any[]): Il2Cpp.Object {
    const img = Il2Cpp.domain.assembly(IMG).image;
    const unitCls = img.class(`${NS_SRV}.AutoChessBattleChessPosUnitInfo`);
    const listCls = img.class("System.Collections.Generic.List`1").inflate(unitCls);
    const list = listCls.alloc() as Il2Cpp.Object;
    list.method(".ctor").invoke();
    const add = list.method("Add");
    for (const u of units) {
        const item = unitCls.alloc() as Il2Cpp.Object;
        item.field("instId").value = u.inst_id | 0;
        item.field("position").value = ((u.row << 16) + u.col) | 0;
        item.field("chessType").value = u.chess_type | 0;
        item.field("direction").value = u.direction | 0;
        add.invoke(item);
    }
    return list;
}

// Build List<short> for diy_ready selectedIdentifiers.
function makeShortList(vals: number[]): Il2Cpp.Object {
    const img = Il2Cpp.domain.assembly(IMG).image;
    const listCls = img.class("System.Collections.Generic.List`1")
        .inflate(img.class("System.Int16"));
    const list = listCls.alloc() as Il2Cpp.Object;
    list.method(".ctor").invoke();
    const add = list.method("Add");
    for (const v of vals) add.invoke(v | 0);
    return list;
}

// Build the 24B AutoChessOperationSnapshot value type.
function makeSnapshot(op: number, sr: number, sc: number,
    er: number, ec: number, dir: number): any {
    const snap = C.Snapshot.alloc() as Il2Cpp.Object;
    const p = snap.unbox ? snap.unbox() : snap;   // value-type data view
    snap.field("operationType").value = op;
    const sp = snap.field("startPosition").value as any;
    const ep = snap.field("endPosition").value as any;
    // value-type field writes go through nested ValueType handles
    sp.field("row").value = sr; sp.field("col").value = sc;
    ep.field("row").value = er; ep.field("col").value = ec;
    snap.field("direction").value = dir;
    return p;
}

Il2Cpp.perform(() => {
    try {
        resolveClasses();
        ready = true;
        log("classes resolved");
    } catch (e) {
        log("resolve failed:", e);
    }
});

rpc.exports = {
    getstate(): any {
        if (!ready) return { in_battle: false, err: "not ready" };
        try { return readState(); }
        catch (e) { return { in_battle: false, err: String(e) }; }
    },

    doop(op: number, sr: number, sc: number,
        er: number, ec: number, dir: number): boolean {
        const m = gm();
        const opMgr = field(m, "playerOpManager") as Il2Cpp.Object;
        const snap = makeSnapshot(op, sr, sc, er, ec, dir);
        call(opMgr, "ConstructOperation", snap);
        return !!call(opMgr, "DoOperation");
    },

    sell(row: number, col: number): boolean {
        const m = gm();
        const opMgr = field(m, "playerOpManager") as Il2Cpp.Object;
        const gp = C.GridPosition.alloc() as any;
        const gv = gp.unbox ? gp.unbox() : gp;
        gp.field("row").value = row;
        gp.field("col").value = col;
        return !!call(opMgr, "DoSellOrDestroy", gv);
    },

    sendreq(name: string, argsJson: string): any {
        if (!ready) return { ok: false, err: "not ready" };
        let args: any = {};
        try { args = argsJson ? JSON.parse(argsJson) : {}; } catch (_) { }
        // requests that carry object lists are special-cased
        if (name === "change_position") {
            try {
                const req = reqClass("AutoChessBattleSceneChangePositionUp").alloc() as Il2Cpp.Object;
                req.field("deployCount").value = args.deploy_count | 0;
                req.field("unitPositionInfo").value = makeUnitList(args.units ?? []);
                C.Service.method("SendRequest")
                    .overload(`${NS_SRV}.AutoChessServiceRequest`).invoke(req);
                return { ok: true };
            } catch (e) { return { ok: false, err: String(e) }; }
        }
        if (name === "use_magic") {
            try {
                const req = reqClass("AutoChessBattleSceneUseMagicUp").alloc() as Il2Cpp.Object;
                req.field("instId").value = args.inst_id | 0;
                req.field("deployCount").value = args.deploy_count | 0;
                req.field("unitPositionInfo").value = makeUnitList(args.units ?? []);
                C.Service.method("SendRequest")
                    .overload(`${NS_SRV}.AutoChessServiceRequest`).invoke(req);
                return { ok: true };
            } catch (e) { return { ok: false, err: String(e) }; }
        }
        if (name === "diy_ready") {
            try {
                const req = reqClass("AutoChessTeamDiyChooseReadyUp").alloc() as Il2Cpp.Object;
                req.field("selectedIdentifiers").value = makeShortList(args.identifiers ?? []);
                C.Service.method("SendRequest")
                    .overload(`${NS_SRV}.AutoChessServiceRequest`).invoke(req);
                return { ok: true };
            } catch (e) { return { ok: false, err: String(e) }; }
        }
        return sendRequest(name, args);
    },

    getteam(): any {
        if (!ready) return { in_team: false, err: "not ready" };
        try { return readTeam(); }
        catch (e) { return { in_team: false, err: String(e) }; }
    },

    ping(): string {
        try { gm(); return "in battle"; } catch (e) { return String(e); }
    },
};
