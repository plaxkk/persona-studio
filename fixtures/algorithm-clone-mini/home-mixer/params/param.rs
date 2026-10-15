// 测试夹具节选，不是上游源码。
// 取自 xai-org/x-algorithm（Apache-2.0）的 home-mixer/params/param.rs，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1，见 fixtures/algorithm-clone-mini/README.md。
// 数值是改过的——正是「改过」才能证明清单里的数来自解析，不是抄来的。
use xai_feature_switches::param;

// 单行写法：上游最常见的形态。
param!(FavoriteWeight, f64, "rust_home_mixer_favorite_weight", 0.99);

// 多行写法。
param!(
    ReplyWeight,
    f64,
    "rust_home_mixer_reply_weight",
    4.0
);
param!(
    BidirectionalFollowReplyWeightBoost,
    f64,
    "rust_home_mixer_bidirectional_follow_reply_weight_boost",
    15.0
);

// bool：要看得出 true 和 false 都被吃下。
param!(
    EnableBidirectionalFollowHydration,
    bool,
    "rust_home_mixer_enable_bidirectional_follow_hydration",
    false
);

// 负的浮点。
param!(
    ReportWeight,
    f64,
    "rust_home_mixer_report_weight",
    -100.0
);

// 解析不出来的字面量：值是**引用另一个常量**。这一类必须落成 null，
// 绝不能顺手填个 0——「我们没解析出来」和「上游就是 0」是两件事。
param!(VqvWeight, f64, "rust_home_mixer_vqv_weight", FixtureUnresolved);

// 冷启动那一组，带位置字段。
param!(
    ColdStartImpressionThreshold,
    u32,
    "rust_home_mixer_cold_start_impression_threshold",
    200
);
param!(
    ColdStartSlotMin,
    u32,
    "rust_home_mixer_cold_start_slot_min",
    15
);
param!(
    ColdStartSlotMax,
    u32,
    "rust_home_mixer_cold_start_slot_max",
    16
);

// 下面两个名字里没有 Weight/Boost/Multiplier/Penalty，所以不触发「必须表态」的
// 那道闸；它们**不在** curation 里，用来验证覆盖报告会点名它们，而不是静默丢掉。
param!(AdsTimeGapTSec, f64, "rust_home_mixer_ads_time_gap_t_secs", 4.0);
param!(EnableAdsSource, bool, "rust_home_mixer_enable_ads_source", true);
