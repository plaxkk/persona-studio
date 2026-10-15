// 测试夹具节选，不是上游源码。见 fixtures/algorithm-clone-mini/README.md。
// 取自 xai-org/x-algorithm（Apache-2.0）的 visibility-filtering/rules/rule_spec/rule.rs，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1。

// 「这条规则对谁生效」是独立的一维。NonFollowers 的意思是：
// 看的人**没关注**这个作者时才生效——关注了就看不到这条限制。
pub(in crate::rules) enum Audience {
    Everyone,
    NonAuthors,
    NonFollowers,
    OnlyAuthor,
}

impl Audience {
    pub(in crate::rules) fn condition(self) -> RuleCond {
        match self {
            Self::Everyone => all![],
            Self::NonAuthors => const { RuleCond::Not(&ViewerIsAuthor.leaf()) },
            Self::NonFollowers => const { all![!ViewerIsAuthor, !ViewerFollowsAuthor] },
            Self::OnlyAuthor => ViewerIsAuthor.leaf(),
        }
    }
}
