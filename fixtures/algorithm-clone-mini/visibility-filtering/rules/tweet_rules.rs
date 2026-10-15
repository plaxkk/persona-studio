// 测试夹具节选，不是上游源码。见 fixtures/algorithm-clone-mini/README.md。
// 取自 xai-org/x-algorithm（Apache-2.0）的 visibility-filtering/rules/tweet_rules.rs，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1。

pub(super) const RULE_EVERYONE_DROP: Rule = Rule {
    id: RuleId::FIXTURE_EVERYONE_DROP,
    audience: Everyone,
    when: any![TweetHasLabel(FIXTURE_LABEL)],
    action: RuleAction::spec(ActionSpec::Drop),
};

pub(super) const RULE_NON_FOLLOWER_DROP: Rule = Rule {
    id: RuleId::FIXTURE_NON_FOLLOWER_DROP,
    audience: NonFollowers,
    when: any![TweetHasLabel(FIXTURE_INSULTS_LABEL)],
    action: RuleAction::spec(ActionSpec::Drop),
};
